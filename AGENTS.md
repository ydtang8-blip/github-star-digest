# Agent 工作约定

## 代码提交
- 每次改完代码，**自动 commit 并 push 到 GitHub**，不用等用户提醒。
- commit message 用中文，风格参考历史提交（如 `feat: ...` / `fix: ...` / `chore: ...`）。
- 修改前端 `web/static/app.js` 或 `web/static/app.css` 后，记得在 `web/templates/index.html` 里把引用版本号 `?v=N` 加一。
- 改完后端（`star_digest/`、`app.py`）需要重启 `uvicorn app:app --port 8787` 服务才生效（reload 关闭）。

## 重启服务
```powershell
Stop-Process -Id <旧PID> -Force
Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList @("-m","uvicorn","app:app","--host","127.0.0.1","--port","8787") -WorkingDirectory "<项目根目录>" -WindowStyle Minimized
```
