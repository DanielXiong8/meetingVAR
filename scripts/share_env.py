"""Encrypt/decrypt .env using a random Fernet key, never printed to stdout."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values

REQUIRED = ("MEETINGVAR_LLM_BASE_URL", "MEETINGVAR_LLM_API_KEY", "MEETINGVAR_LLM_MODEL")


def _new_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwrite; mode restricts permissions on Unix. On Windows the
    # user's directory ACL applies, so keep .secrets in a private user folder.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)


def encrypt(env: Path, output: Path, key_file: Path) -> None:
    paths = [p.resolve() for p in (env, output, key_file)]
    if len(set(paths)) != 3:
        raise ValueError("明文、密文、密钥必须使用三个不同的路径")
    if output.suffix != ".enc":
        raise ValueError("密文文件必须使用 .enc 扩展名")
    if ".secrets" not in key_file.resolve().parts or key_file.suffix != ".key":
        raise ValueError("加密密钥必须保存到 .secrets 目录中的 .key 文件，避免误入库")
    if output.exists() or key_file.exists():
        raise FileExistsError("密文或密钥文件已存在；请换用新文件名，不会覆盖已有文件")
    if env.stat().st_size > 1024 * 1024:
        raise ValueError(".env 超过 1 MiB，拒绝作为配置分享")
    values = dotenv_values(env, encoding="utf-8-sig", interpolate=False)
    missing = [name for name in REQUIRED if not str(values.get(name) or "").strip()]
    if missing:
        raise ValueError("请先填写配置项：" + ", ".join(missing))
    key = Fernet.generate_key()
    encrypted = Fernet(key).encrypt(env.read_bytes())
    # The decryption key must remain local and be sent separately by the user.
    _new_file(key_file, key + b"\n")
    _new_file(output, encrypted + b"\n")


def decrypt(encrypted: Path, output: Path, key: bytes) -> None:
    if output.exists():
        raise FileExistsError("输出文件已存在；请使用 --output .env.received，核对后自行替换")
    if encrypted.resolve() == output.resolve():
        raise ValueError("解密输出不能覆盖密文")
    try:
        plaintext = Fernet(key.strip()).decrypt(encrypted.read_bytes().strip())
    except (InvalidToken, ValueError) as exc:
        raise ValueError("解密失败：密钥不正确或密文被修改；未写出明文") from exc
    _new_file(output, plaintext)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    enc = sub.add_parser("encrypt", help="加密填写好的 .env，生成独立随机密钥")
    enc.add_argument("--env", type=Path, default=Path(".env"))
    enc.add_argument("--output", type=Path, default=Path("meetingvar.env.enc"))
    enc.add_argument("--key-file", type=Path, default=Path(".secrets/meetingvar-env.key"))
    dec = sub.add_parser("decrypt", help="解密；未提供密钥文件时隐藏输入密钥")
    dec.add_argument("--input", type=Path, default=Path("meetingvar.env.enc"))
    dec.add_argument("--output", type=Path, default=Path(".env"))
    dec.add_argument("--key-file", type=Path, default=None)
    args = parser.parse_args()
    try:
        if args.command == "encrypt":
            encrypt(args.env, args.output, args.key_file)
            print(f"密文已生成：{args.output}（可提交 Git）")
            print(f"密钥已保存在：{args.key_file}（禁止提交 Git，请通过另一渠道私发）")
        else:
            key = args.key_file.read_bytes() if args.key_file else getpass.getpass("解密密钥（输入隐藏）: ").encode("ascii")
            decrypt(args.input, args.output, key)
            print(f"已解密到：{args.output}（明文不得提交 Git）")
    except (OSError, ValueError, UnicodeError):
        # Never echo provider credentials, decrypted content, or key bytes.
        # Validation messages below contain only field names and static text.
        raise SystemExit("操作失败：请确认三项配置已填完整、输入文件/密钥有效，且输出文件不存在；不会覆盖文件。")


if __name__ == "__main__":
    main()
