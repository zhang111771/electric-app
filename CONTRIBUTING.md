# 贡献指南

感谢你愿意为这个项目添砖加瓦！任何形式的贡献都欢迎：提 Issue、修 Bug、加功能、改进文档。

## 快速开始

```bash
# 后端
cd electric_langgraph
cp .env.example .env        # 填入你的 LLM_API_KEY
bash scripts/run_local.sh

# 前端（可选）
cd electric-agent
npm install && npm run dev
```

详细说明见各子项目 README：[electric_langgraph/README.md](electric_langgraph/README.md)、[electric-agent/README.md](electric-agent/README.md)。

## 报告 Bug / 提需求

- 先搜索 [Issues](https://github.com/zhang111771/electric-app/issues)，避免重复。
- 新建 Issue 时请使用模板，尽量附上：
  - 运行环境（OS / Python 版本 / Docker 或本地运行）
  - 复现步骤
  - 相关日志或截图

## 提交代码

1. 先 [Fork](https://github.com/zhang111771/electric-app/fork) 本仓库，`git clone` 你的副本。
2. 新建分支：`git checkout -b feat/你的功能名`（或 `fix/...`、`docs/...`）。
3. 编写代码并**本地跑通**（后端启动 + 一次对话；前端 `npm run build` 通过）。
4. 提交信息用中文或英文均可，建议写清改动内容：
   ```bash
   git add .
   git commit -m "feat: 新增 xxx 能力"
   ```
5. push 到你的远端，向 `main` 分支发起 Pull Request，描述里说明改动与测试情况。

## 代码规范

- Python 遵循 [PEP 8](https://peps.python.org/pep-0008/)，注释用中文。
- 前端 TypeScript + Vue3 组合式 API。
- 改动尽量小而聚焦，方便 review。

## ⚠️ 安全红线

- **任何 API Key / 密码只写进 `.env`（已被 .gitignore 排除），绝不硬编码或提交。**
- 提交前建议自查：`git status` 里不应出现 `.env`、`.mcp.json`、`.claude/`、内部语料等文件。
- 公司内部材料（如 `data/assets/files/` 下的作业指导书）一律不得上传。

## License

参与贡献即视为同意你的代码以本仓库 [MIT License](LICENSE) 授权发布。
