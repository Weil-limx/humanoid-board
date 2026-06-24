#!/usr/bin/env python3
"""TronCamp / Humanoid 提交 CLI（GitHub-as-Gateway）。

把选手的推理代码 + 策略权重提交到 GitHub 网关仓库：
  - 代码打包成 code.tar.gz、权重 policy.pt 作为该次提交的 Release asset 上传（大文件不进 git）；
  - meta.json commit 到 submissions/<team>/<id>/，触发网关 Actions 做令牌/合法性校验 + 入队（当前不限提交次数）。

查询：
  submit.py --status --repo <owner>/<repo> --token <PAT> --team <队伍>
读 submissions/<team>/ 下各 status.json（评测 Worker 回写的 queued/running/done/failed + 分数）。

依赖：无（仅 Python 3 标准库 urllib）。选手只要有 python3 即可运行，无需 pip install。
鉴权：每队 GitHub fine-grained PAT（Contents: RW，涵盖 release 操作）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import time
import uuid

import urllib.error
import urllib.parse
import urllib.request

GITHUB_API = "https://api.github.com"
MAX_CKPT_BYTES = 500 * 1024 * 1024  # 500 MB
CLI_VERSION = "1.0.0"


# ----------------------------- 纯工具函数（可独立单测，无网络） -----------------------------
def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_code_tarball(code_dir: str) -> bytes:
    """把 code_dir 打包成 tar.gz 字节流；目录/文件名排序保证可复现。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(code_dir):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(root, name)
                tar.add(full, arcname=os.path.relpath(full, code_dir))
    return buf.getvalue()


def new_submission_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


# ----------------------------- GitHub 网关薄封装 -----------------------------
class GatewayClient:
    """对 GitHub 网关仓库的 REST 封装（建 release / 传 asset / commit 文件 / 读状态）。仅用标准库 urllib。"""

    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.token = token

    def _headers(self, extra=None):
        h = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "troncamp-submit",
        }
        if extra:
            h.update(extra)
        return h

    def _request(self, method, url, *, headers=None, json_body=None, raw_body=None,
                 timeout=60, ok_404=False):
        """发一个请求，返回解析后的 JSON（无内容则 None）。ok_404=True 时 404 返回 None 而非抛错。"""
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers = {**(headers or {}), "Content-Type": "application/json"}
        else:
            data = raw_body
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code == 404 and ok_404:
                return None
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"GitHub API {method} {url} -> {e.code}: {detail}") from None

    def whoami(self) -> dict:
        return self._request("GET", GITHUB_API + "/user", headers=self._headers(), timeout=30)

    def create_release(self, tag: str, name: str) -> dict:
        return self._request(
            "POST", f"{GITHUB_API}/repos/{self.repo}/releases", headers=self._headers(),
            json_body={"tag_name": tag, "name": name, "body": "TronCamp submission asset bundle."})

    def upload_asset(self, upload_url: str, asset_name: str, data: bytes, content_type: str) -> dict:
        base = upload_url.split("{", 1)[0]  # 去掉 "{?name,label}" 模板尾
        url = base + "?" + urllib.parse.urlencode({"name": asset_name})
        return self._request(
            "POST", url, headers=self._headers({"Content-Type": content_type}),
            raw_body=data, timeout=300)

    def put_file(self, path: str, content: bytes, message: str) -> dict:
        return self._request(
            "PUT", f"{GITHUB_API}/repos/{self.repo}/contents/{path}", headers=self._headers(),
            json_body={"message": message, "content": base64.b64encode(content).decode("ascii")})

    def list_dir(self, path: str) -> list:
        out = self._request(
            "GET", f"{GITHUB_API}/repos/{self.repo}/contents/{path}",
            headers=self._headers(), timeout=30, ok_404=True)
        return out or []

    def get_json(self, path: str):
        return self._request(
            "GET", f"{GITHUB_API}/repos/{self.repo}/contents/{path}",
            headers=self._headers({"Accept": "application/vnd.github.raw+json"}),
            timeout=30, ok_404=True)


# ----------------------------- 命令实现 -----------------------------
def do_submit(args) -> int:
    if not os.path.isfile(args.ckpt_file):
        print(f"错误：权重文件不存在：{args.ckpt_file}", file=sys.stderr); return 2
    size = os.path.getsize(args.ckpt_file)
    if size > MAX_CKPT_BYTES:
        print(f"错误：权重 {size/1e6:.1f}MB 超过上限 {MAX_CKPT_BYTES/1e6:.0f}MB", file=sys.stderr); return 2
    if not os.path.isdir(args.code_dir):
        print(f"错误：代码目录不存在：{args.code_dir}", file=sys.stderr); return 2

    sub_id = new_submission_id()
    tag = f"sub-{args.team}-{sub_id}"
    code_tar = make_code_tarball(args.code_dir)
    code_sha = sha256_bytes(code_tar)
    ckpt_sha = sha256_file(args.ckpt_file)

    gw = GatewayClient(args.repo, args.token)
    rel = gw.create_release(tag, f"{args.team} {sub_id}")
    with open(args.ckpt_file, "rb") as f:
        gw.upload_asset(rel["upload_url"], "policy.pt", f.read(), "application/octet-stream")
    gw.upload_asset(rel["upload_url"], "code.tar.gz", code_tar, "application/gzip")

    meta = {
        "team": args.team, "submission_id": sub_id,
        "competition": args.competition, "task": args.task,
        "ckpt_release_tag": tag, "ckpt_sha256": ckpt_sha, "code_sha256": code_sha,
        "config_path": args.config_path,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cli_version": CLI_VERSION,
    }
    gw.put_file(
        f"submissions/{args.team}/{sub_id}/meta.json",
        json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
        f"submit({args.competition}): {args.team}/{sub_id}",
    )
    print(f"✓ 已提交：{args.team}/{sub_id}（task={args.task}）")
    print(f"  权重 sha256: {ckpt_sha[:16]}…  代码 sha256: {code_sha[:16]}…")
    print(f"  查结果：submit.py --status --repo {args.repo} --team {args.team} --token <PAT>")
    return 0


def do_status(args) -> int:
    gw = GatewayClient(args.repo, args.token)
    entries = gw.list_dir(f"submissions/{args.team}")
    subs = sorted(e["name"] for e in entries if e.get("type") == "dir")
    if not subs:
        print(f"（{args.team} 暂无提交）"); return 0
    print(f"{args.team} 的提交（{len(subs)} 条）：")
    for sid in subs:
        st = gw.get_json(f"submissions/{args.team}/{sid}/status.json")
        if not st:
            print(f"  {sid}  queued（尚未评测）"); continue
        line = f"  {sid}  {st.get('status', '?')}"
        if st.get("total") is not None:
            line += f"  total={st['total']}"
        if st.get("error"):
            line += f"  ({st['error']})"
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TronCamp / Humanoid 提交 CLI（GitHub 网关）")
    p.add_argument("--repo", required=True, help="GitHub 网关仓库 owner/name，如 weil/troncamp-submissions")
    p.add_argument("--token", required=True, help="队伍 GitHub PAT（Contents: RW）")
    p.add_argument("--team", required=True, help="队伍标识")
    p.add_argument("--status", action="store_true", help="查询本队提交状态（而非提交）")
    p.add_argument("--competition", default="tron", choices=["tron", "humanoid"], help="赛题")
    p.add_argument("--task", default="ATEC-TaskC-Tron2ALegged", help="task id")
    p.add_argument("--ckpt-file", help="策略权重 policy.pt")
    p.add_argument("--code-dir", help="推理代码目录")
    p.add_argument("--config-path", default=None, help="可选 config 路径（包内）")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.status:
        return do_status(args)
    if not args.ckpt_file or not args.code_dir:
        print("错误：提交需要 --ckpt-file 和 --code-dir", file=sys.stderr); return 2
    return do_submit(args)


if __name__ == "__main__":
    sys.exit(main())
