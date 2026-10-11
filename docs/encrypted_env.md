# 加密分享 API 配置

## 文件职责

| 文件 | 内容 | 是否入 Git |
|---|---|---|
| `.env.example` | 空模板，不含任何凭据 | 是 |
| `.env` | 用户填写后的本机明文配置 | 否，已忽略 |
| `meetingvar.env.enc` | 填好后生成的认证加密密文 | 可以，由用户确认后提交 |
| `.secrets/meetingvar-env.key` | 随机生成的解密密钥 | 绝不提交；由用户单独私发 |

加密采用标准库 `cryptography` 的 Fernet 认证加密，随机密钥由库生成，不使用自创算法或简单 Base64 混淆。
错误密钥、被修改的密文均拒绝解密，不写出未经验证的明文。所有工具默认拒绝覆盖已有文件。
算法接口与边界见 [cryptography 官方文档](https://cryptography.io/en/latest/fernet/)。

## 发送方

从仓库根目录执行，使用已有 Python 3.10 环境：

```powershell
python -m pip install -e . -r requirements-secrets.txt
# 首次使用：复制空模板；若已有 .env，不要覆盖它。
# Copy-Item .env.example .env
```

本机若已由助手创建 `.env`，直接打开填写三项：

```dotenv
MEETINGVAR_LLM_BASE_URL=
MEETINGVAR_LLM_API_KEY=
MEETINGVAR_LLM_MODEL=
```

云端基础地址应使用 HTTPS，不要包含末尾 `/chat/completions`。含特殊字符的值可以用单引号包裹。
程序启动时自动读取**当前工作目录**的 `.env`；已有系统/进程环境变量优先，不自动查找父目录，不执行文件内代码、不展开 `${...}`。

填写完整后：

```powershell
python scripts/share_env.py encrypt
git check-ignore .env .secrets/meetingvar-env.key
git add meetingvar.env.enc
git diff --cached --stat
git commit -m "chore: share encrypted meetingvar configuration"
git push origin codex/b-feedback-loop
```

工具只打印文件位置，不打印 API 凭据或随机密钥。三个必要值未填写时拒绝加密，不会生成假交付物。
把 `.secrets/meetingvar-env.key` 文件通过另一条私密渠道交给指定协作者；**不要与密文一起提交 Git，不要贴进 PR、issue 或公开聊天**。
若密文或密钥已存在，使用新的配对文件名，工具不会覆盖：

```powershell
python scripts/share_env.py encrypt --output meetingvar-v2.env.enc --key-file .secrets/meetingvar-env-v2.key
```

## 接收方

获取已提交密文的分支，安装工具依赖，然后运行：

```powershell
python -m pip install -e . -r requirements-secrets.txt
python scripts/share_env.py decrypt
```

按提示粘贴发送方私发的密钥，输入隐藏，密钥不出现在命令行历史里。
也可以把收到的密钥文件放入已忽略的 `.secrets/` 目录后执行：

```powershell
python scripts/share_env.py decrypt --key-file .secrets/meetingvar-env.key
```

若本机已有 `.env`，工具拒绝覆盖。请先输出到 `.env.received`，自行核对/备份后再替换：

```powershell
python scripts/share_env.py decrypt --output .env.received
```

解密后在仓库根目录启动项目即可自动加载 `.env`。真实语音模型和设备仍须另行配置；API 配置不是模型权重。

## 安全边界

- 任何拿到解密密钥的人都能解密该密文；请只发给必要协作者。
- 本机 `.env` 和密钥文件仍是明文。`.gitignore` 防误提交，不是操作系统级加密或权限隔离；Windows 请使用自己的私密用户目录。
- Fernet 密文会暴露生成时间及近似大小，不隐藏“存在一份配置”这一事实。
- 提交过的密文可能永久留在 Git 历史。若密钥或 API 凭据泄露，应在服务商撤销/轮换 API Key，再使用新随机密钥重新加密；仅删文件不能撤销已取得的权限。
- 不要使用 `git add -f .env` 或强制添加 `.secrets/`。工具不自动执行 Git 操作，也不自动发送密钥。

## 本轮验证（2026-10-11）

完整 Python 3.10 环境 37 项测试全部通过，其中新增 5 项覆盖加解密往返、错误密钥、密文篡改、拒绝覆盖/空配置、环境变量优先及不展开敏感值。
无可选依赖的干净目录测试为 19 项通过、18 项明确跳过；编译检查通过。
已用 `git check-ignore` 确认 `.env`、`.env.received`、`.secrets/meetingvar-env.key` 被忽略，空模板及密文目标不被忽略。
本轮尚未填写或分享真实凭据；没有生成真实密文，也没有将解密密钥上传。
