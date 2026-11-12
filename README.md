# Invoice · 发票与费用管理

中文界面，仅使用加元 CAD。将扫描发票变成可核对、可搜索、可统计的个人账本。应用使用 Python / FastAPI、SQLite、Tesseract OCR 和原生前端；无需付费 AI API。

## 功能

- 批量上传 PDF、JPG、PNG、WebP；单个文件最大 20 MB，PDF 最多 20 页。
- 提取发票号码、日期、金额，尝试识别商户、费用类别和标注用途（Description / Purpose）；均可手动修改。每条记录有独立系统 ID。
- 上传后持久保存为「待核对」，编辑并确认后计入报表；支持手动录入。
- 按日期、金额和录入时间排序；搜索商户、号码、用途；按类别、状态、币种、时段筛选。
- 按月、按年、自定义日期范围统计支出、每月趋势与类别构成；导出 UTF-8 CSV 明细和月度类别报表，可用 Excel 打开。
- 所有发票、录入、报表和 CSV 均使用加币 CAD。升级启动时，会将现有记录的币种标签统一为 CAD，金额数值保持不变；适用于本账本所有原始发票均为加币的情况，不进行汇率换算。
- 在列表点击「查看 / 核对」或「打开原件」可随时复查附件；PDF 提供首页图片预览，原件可查看全部页。待核对记录支持「重新识别缺失信息」，保留已填写字段。
- 保存原始附件，文件哈希去重；本地数据库长期保存。删除记录会同时删除其附件。
- 可选应用密码保护，启用后首页、文件、查询及导出均需认证。

## 本地运行

需要 Python 3.12 和 Tesseract。Ubuntu / Debian 安装 OCR：

```bash
sudo apt-get update
sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim
```

macOS 可使用 `brew install tesseract tesseract-lang`。Windows 安装 Tesseract 并将程序目录加入 PATH，同时安装 `chi_sim` 和 `eng` 语言数据。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

打开 **http://127.0.0.1:8000**。Windows 激活命令为 `.venv\Scripts\activate`。

默认无密码，供本人在本机使用。设置 `APP_PASSWORD` 环境变量可启用浏览器登录，用户名任意，密码为设定值。例如 macOS / Linux：

```bash
export APP_PASSWORD='请换成自己的长密码'
uvicorn app:app --host 127.0.0.1 --port 8000
```

`DATA_DIR` 环境变量可更改数据目录，默认 `./data`。直接运行不会自动读取 `.env`；可使用 `uvicorn app:app --env-file .env` 并安装 `python-dotenv`，或使用下面的 Docker 方式。

## Docker 部署（包含中英文 OCR）

```bash
cp .env.example .env
# 编辑 .env，将 APP_PASSWORD 改为自己的长密码
docker compose up -d --build
```

访问 **http://localhost:8000**，用户名任意、密码为 `.env` 中的值。数据存入 Docker 持久卷 `invoice-data`；重建容器不丢失数据。不要执行 `docker compose down -v`，该命令会删除数据卷。

### 外网使用

GitHub 保存源代码，GitHub Pages 不能运行本应用的 Python 后端、OCR 和数据库。把此项目部署到支持 Docker 的服务器或主机，持久挂载 `/data`，设置 `APP_PASSWORD`，再通过带有效证书的 HTTPS 反向代理访问。Compose 默认只监听服务器本机 8000 端口，可接 Caddy / Nginx 转发。不要在没有 HTTPS 的公网连接上使用密码认证。

这是单用户应用，使用一个共享应用密码；不包含多租户、企业权限、审批流或税务申报功能。个人凭证请部署在自己控制的服务器。原件和识别文本不会发送到外部 OCR 服务。

## 备份与恢复

本地运行：停止应用，将整个 `data/` 目录复制到安全位置（包含 `invoices.sqlite` 与 `files/`），再启动。恢复时停止应用并用备份完整替换数据目录。CSV 是分析导出，不包含附件，不能代替完整备份。

Docker：先 `docker compose stop`，再执行以下命令将持久数据备份到当前目录：

```bash
docker compose run --rm --no-deps --user root -v "$PWD:/backup" invoices python -c "import shutil; shutil.make_archive('/backup/invoice-backup', 'gztar', '/data')"
docker compose start
```

备份包含财务原件，请妥善保管。恢复时先停止应用，再把压缩包内容完整解压到该服务挂载的 `/data` 卷，并确保文件归容器内 `invoice` 用户所有。

## 使用流程与识别限制

1. 上传一张或多张扫描件，系统逐张识别并保存为待核对。
2. 打开记录，对照原件检查发票号码、商户、日期、金额、币种、费用类别与用途。
3. 点击「确认入账」。缺少商户、有效日期或正数金额时无法确认。
4. 进入「分析报表」，选择月份、年份或起止日期；导出报表或全部筛选明细。

本地 OCR 采用文字识别与规则提取，不保证准确率。商户识别优先考虑顶部文字，结合文字大小判断突出店名；带文本的 PDF 还会利用粗体信息。图片 OCR 采用文字高度近似字号，不能可靠判断粗体。日期优先选择发票日期并降低到期日期的优先级，支持英文月份、年/月/日、月/日/年及可明确判断的日/月/年。11/02/2026 等歧义数字日期按月/日/年解析，请对照原件核对。模糊照片、手写凭证、复杂表格仍可能需要手动补充。不要把自动识别值当作已审核金额。中文扫描件需要 `chi_sim` 语言包；Docker 已包含。如果一份 PDF 包含多张独立发票，请先拆分，当前每个上传文件对应一条发票记录。OCR 类别是建议，用途请自己确认。

## 验证

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

测试覆盖真实图片 OCR、PDF 识别、确认前后统计、仅加币校验、顶部商户和多种日期格式、重新识别、附件预览、日期筛选、CSV 导出、重复文件、输入验证、密码保护和删除附件。

## 上传到 GitHub 的 Invoice 仓库

在项目目录执行（须先安装 GitHub CLI 并登录）：

```bash
git init -b main
git add .
git commit -m "Build Invoice expense manager"
gh auth login
gh repo create Invoice --private --source=. --remote=origin --push
```

如果已有仓库，请改用已有仓库地址配置 `origin` 后推送。`.gitignore` 排除了 `.env`、数据目录与附件，不要提交凭证、密码或真实发票。建议仓库保持私有。
