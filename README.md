# GitHub 高星日报

每天自动汇总 GitHub 趋势榜和近一周新晋高星项目。勾选有用的之后，会下载到本地，并接到你自己的 GitHub 仓库。

## 每天怎么用

1. 双击桌面上的 **打开GitHub高星日报**
2. 浏览器打开 `http://127.0.0.1:8787`
3. 右上角 **设置** 粘贴 GitHub Token（勾选 `repo`），点 **连接并同步到我的 GitHub**
4. 看中文摘要，勾选有用的
5. 点底部 **下载并接到我的 GitHub**

每个选中的项目会：

- 下载到 `文档\github-stars`
- fork 到你的 GitHub 账号
- 本地 `origin` 指向你的 fork，`upstream` 指向原仓库
- 写进你的总仓库 `github-star-picks`（`CATALOG.md` + `picks.json`）

日报程序本身会推到你的 `github-star-digest` 仓库。

## 自动采集

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\install-daily-task.ps1"
```

每天 08:00 采集。关机错过了，下次开机连上网会补跑。

## Token

到 [Classic Token 页面](https://github.com/settings/tokens/new) 新建，勾选 **`repo`**（创建仓库、fork、推送都需要）。细粒度 Token 默认不能新建仓库。

中文摘要默认走 **DeepSeek**。在设置里填 `DEEPSEEK_API_KEY`，或到 [platform.deepseek.com](https://platform.deepseek.com/api_keys) 创建后粘贴。填好后点「用 DeepSeek 写中文摘要」。

## 手动命令

```powershell
.\.venv\Scripts\python.exe -m star_digest collect --force
.\.venv\Scripts\python.exe -m star_digest connect
.\.venv\Scripts\python.exe app.py
```
