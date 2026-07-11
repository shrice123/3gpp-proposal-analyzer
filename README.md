# 3GPP 提案洞察

一个本地优先的桌面工具，用于导入 3GPP 会议目录、按多个 agenda 和母公司归组后的首个 source 筛选提案、打包下载原始 ZIP、后台预处理 Word/PPT/PDF/Excel 文稿、进行带出处的问答，并在对话中生成 Word 或 PowerPoint 报告。提案详情和导出文件仍保留完整的同签来源。

## 下载安装

从 [Releases](https://github.com/shrice123/3gpp-proposal-analyzer/releases) 页面下载最新版本：

| 文件 | 平台 | 说明 |
|------|------|------|
| `3GPP-Proposal-Analyzer-Online-*-mac-arm64.dmg` | macOS (Apple Silicon) | 安装包，内置 Electron 桌面壳 |
| `3GPP-Proposal-Analyzer-Offline-*-mac-arm64.dmg` | macOS (Apple Silicon) | 离线安装包，内置当前 runtime-bundle |
| `3GPP-Proposal-Analyzer-Online-*-win-x64.exe` | Windows x64 | 安装包，需联网获取文档转换组件 |
| `3GPP-Proposal-Analyzer-Offline-*-win-x64.exe` | Windows x64 | 离线安装包，内置当前 runtime-bundle |
| `3GPP-Proposal-Analyzer-Portable-Windows-x64.zip` | Windows x64 | ⚡ 免安装便携版，解压双击 `start.bat` 即用 |

> 国内下载慢？把链接中 `github.com` 替换为 `ghproxy.com/https://github.com/...` 即可加速。

### 便携版使用

1. 解压 ZIP 到任意文件夹
2. 双击 `start.bat`
3. 等待终端显示「Backend ready」→ 浏览器自动打开 `http://localhost:3000`
4. 关闭命令行窗口即停止所有服务

> 便携版无需 Python，使用 Windows 自带的 PowerShell 提供前端服务。

## 给普通用户

普通用户不需要 Docker、Python、Node.js 或命令行。

在当前 macOS 开发目录中，可以直接双击 `启动提案分析工具.command`。脚本会按需构建最新网页，只启动一个本地服务并打开 `http://127.0.0.1:8765/`；保留启动窗口，按 `Control+C` 可停止服务。

1. Windows 用户运行 `3GPP-Proposal-Analyzer-Online-*.exe`，macOS 用户打开对应的 DMG。
2. 从桌面图标启动软件。
3. 首次使用向导会检查本机环境，并允许配置云端大模型。
4. 将 3GPP 会议的 `Docs` 目录链接粘贴到“会议提案来源”。
5. 使用可搜索多选的 agenda/source 筛选并勾选提案；应用会自动在后台下载和提取文稿。公司名称变体会自动归并，也可在设置中修正别名。
6. 直接在右侧提问，应用会补齐模型分析并持续显示处理进度；输入“生成报告”或 `/ppt` 可先编辑大纲，再生成、预览和下载文件。“下载所选提案”用于另行生成原始 ZIP 总包。

对话支持多个独立会话、会话级模型选择、实时流式回答和一键终止。普通大范围问题默认采用自适应证据检索；报告、逐篇分析和明确要求全面覆盖的问题自动使用全量分析。报告会同时使用当前会话历史和提案证据。

在线安装包在首次运行时获取文档转换和 OCR 组件。当前仓库的 `runtime-bundle/` 仅包含 manifest 和说明文件，因此离线安装包会按离线结构打包，但实际转换/OCR组件仍可能需要下载。生产安装包需要发布方提供签名和经过 SHA-256 校验的运行组件清单。

## 开发与验证

### 本地开发

```bash
npm install
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

日常源码测试直接双击 `启动提案分析工具.command`，不需要启动两个终端。只有调试前端热更新时才分别运行：

```bash
.venv/bin/python backend/run.py
npm run dev
```

桌面渲染器和 Electron：

```bash
npm run dev:renderer
PROPOSAL_RENDERER_URL=http://localhost:5173 PROPOSAL_PYTHON=.venv/bin/python npm run dev:desktop
```

### 构建

```bash
npm run build
npm run build:renderer
npm run build:backend
npm run build:desktop
```

macOS Apple Silicon arm64 与 Windows x64 安装包由 GitHub Actions 在对应系统上分别构建。在线版配置见 `electron-builder.online.yml`，离线版配置见 `electron-builder.offline.yml`。

### 测试

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
npm test
npm run build:renderer
npm run test:full
```

## 安全与限制

- 本地 API 只监听 `127.0.0.1`，不默认开放局域网或公网。
- 3GPP URL 只允许 `https://www.3gpp.org/ftp/...`，避免下载代理被滥用。
- 压缩包解压带路径穿越、文件数量和展开大小限制。
- API Key 使用本机主密钥加密，日志和诊断包均不包含密钥。
- 原生 Office 图表、常见流程图和 OCR 证据优先在本地结构化；只有实际存在无法本地恢复的复杂素材时才使用视觉模型或显示覆盖不足提示。
- 参考 Word/PPT 缺少可复用结构时会给出兼容性警告；用户需明确启用风格扩展。
- 在线模型调用受供应商配额、限流和费用约束。

## 项目结构

- `app/`：共享的 React 工作区界面。
- `backend/`：FastAPI、SQLite、后台预处理、下载/分析和报告服务。
- `desktop/`：Electron 主进程、预加载和桌面渲染入口。
- `scripts/`：PowerPoint 生成及发布辅助脚本。
- `runtime-bundle/`：离线安装包运行组件清单与待签名组件。
