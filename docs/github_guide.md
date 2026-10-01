# GitHub 速成指南（为本项目定制）

> 面向 DanielXiong8（Owner）和 Qyc314159（Collaborator）。目标：只学这个项目用得上的 20%。

## 1. 心智模型（一张图）

```
你的电脑 (working dir)  --add/commit-->  本地仓库  --push-->  GitHub (origin)
你的电脑 (working dir)  <--pull--------  本地仓库  <--fetch--  GitHub (origin)
```

- **commit** = 本地存档点（快照），**push** = 把存档点传上云端，**pull** = 把云端的新东西拉下来
- **branch（分支）** = 平行世界：在分支上改代码不影响 main
- **Pull Request（PR）** = "请求把我的平行世界合并回 main"，队友在网页上审查后点 **Merge** 完成合并
- **origin** = GitHub 上这个仓库的代称

## 2. 每日流程（背下来就这三句）

```bash
git pull                 # 开工第一件事，永远先 pull 再写代码
# ...写代码/改文档...
git add -A && git commit -m "feat: 一句话说清改了什么" && git push   # 收工
```

代码改动走分支（PR 流程，见 §4）；文档小改可以直接 main。

## 3. GitHub 网页功能地图（按对本项目的有用程度排序）

| 功能 | 在哪 | 干什么用 |
|---|---|---|
| **Pull requests** | 顶部标签 | 审查代码、点 Merge。PR #1 就是范例 |
| **Issues** | 顶部标签 | 团队待办/bug 清单 = **我们的 backlog 唯一入口**。新想法开 Issue → 周日联调节点决定做不做 → 做完关掉。可指派给人、贴标签 |
| **Commits** | Code 页右上角时钟图标 | 全部历史 = 时间机器，也是"已完成/未完成"的诚实证据（比赛硬性要求） |
| **Insights → Contributors** | 左侧栏 | 两人贡献图与统计，**答辩 PPT 直接截图** |
| **Code + Blame** | Code 标签 | 浏览文件；某文件点 History 看演变，Blame 看每行是谁写的 |
| **Settings** | 顶部最右 | 管理协作者（Collaborators）、改名/删除（Danger Zone）、决赛后转公开也在这 |
| **Actions** | 顶部标签 | CI 自动化（push 自动跑测试），现在用不上，决赛前可选配 |
| **Projects / Wiki / Fork / Star** | — | 看板/维基/复制到别人名下/点赞——两人小队用不上，忽略 |

## 4. PR 流程（改代码的标准路径）

```bash
git checkout -b feat/xxx     # 开分支
# ...改代码...
git add -A && git commit -m "feat: xxx"
git push -u origin feat/xxx  # 推分支
```

然后网页打开仓库 → Pull requests → **New pull request** → base: main ← compare: feat/xxx → Create。
PR 页面三个区：**Conversation**（讨论+Merge 按钮）、**Commits**（提交列表）、**Files changed**（逐行对比，可对某行留评论）。
Merge 后点提示里的 **Delete branch** 清理。PR 描述里写 `Closes #3` 可在合并时自动关掉 3 号 Issue。

## 5. 本项目约定

1. `main` = 随时可看的最新状态；代码一律走分支 + PR；`docs/` 小改可直接 main
2. commit message：英文、小写开头、一句话（如 `feat(c1): offline acceptance script`，看 git log 学样例）
3. **Issue = backlog 唯一入口**（对应每日计划的"新想法一律进 backlog，周日再议"）
4. `.gitignore` 已挡住 `data/`（他人声音/屏幕）与 `models/`（GB 级权重）——**不要用 `git add -f` 绕过它**
5. 谁踩坑谁写 SETUP.md，和代码同一提交

## 6. 出问题了怎么办

- `git pull` 报 **conflict**：不要慌，把报错发群里；或 VS Code 打开冲突文件用合并编辑器选保留哪边，改完 add+commit
- push 被拒（`rejected`）：先 `git pull` 再 push
- 误提交了大文件/敏感文件：立刻说，历史还没被别人拉走前都好收拾
- 更诡异的问题：截屏发群里，两个人总有一个见过
