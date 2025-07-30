# 使用 UV 管理项目

本项目已配置支持 UV，这是一个极快的 Python 包管理器。

## 安装 UV

首先安装 UV（如果尚未安装）：

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 或者使用 pip
pip install uv
```

## 创建虚拟环境并安装依赖

```bash
# 创建虚拟环境（基于 .python-version 中指定的版本）
uv venv

# 激活虚拟环境
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 安装所有依赖
uv pip install -e .

# 或者直接使用 uv sync（推荐）
uv sync
```

## 运行应用

```bash
# 确保在虚拟环境中
python app.py

# 或者使用 uv 直接运行
uv run python app.py
```

## 添加新依赖

```bash
# 添加生产依赖
uv add requests

# 添加开发依赖  
uv add --dev pytest

# 添加特定版本
uv add "flask>=2.3.0"
```

## 删除依赖

```bash
uv remove requests
```

## 更新依赖

```bash
# 更新所有依赖
uv pip install --upgrade -e .

# 或者使用 sync 更新
uv sync --upgrade
```

## 导出 requirements.txt（如需要）

```bash
uv pip freeze > requirements.txt
```

## 环境配置

在运行应用之前，请确保创建 `.env` 文件并配置必要的环境变量：

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入你的配置
```

## 项目结构

- `pyproject.toml` - 项目配置和依赖定义
- `.python-version` - 指定 Python 版本
- `app.py` - 主应用文件
- `.env` - 环境变量（需要手动创建）

## 常用命令

```bash
# 检查项目状态
uv status

# 清理缓存
uv cache clean

# 显示依赖树
uv pip list

# 运行测试（如果有的话）
uv run pytest
```

## 故障排除

1. 如果遇到依赖冲突，尝试：
   ```bash
   uv pip install --force-reinstall -e .
   ```

2. 如果 Python 版本问题：
   ```bash
   uv python install 3.11
   uv venv --python 3.11
   ```

3. 如果需要重新创建环境：
   ```bash
   rm -rf .venv
   uv venv
   uv sync
   ``` 