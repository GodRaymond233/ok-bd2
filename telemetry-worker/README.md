# ok-bd2 Telemetry Worker (Cloudflare Workers + D1)

极简匿名版本与活跃统计接收端，自带中文数据看板。

## 1. 本地测试

```powershell
cd telemetry-worker
npm test
```

## 2. 首次部署到 Cloudflare（约 5 分钟）

```powershell
cd telemetry-worker
npm install
npx wrangler login

# 1) 创建 D1 数据库，将输出的 database_id 填入 wrangler.toml
npx wrangler d1 create ok-bd2-telemetry

# 2) 初始化远程表结构
npm run db:init:remote

# 3) 设置看板与 /v1/stats 接口的访问密钥（自己生成一串随机长密码）
npx wrangler secret put ADMIN_TOKEN

# 4) 在 wrangler.toml 中取消 [[routes]] 注释，绑定你在 Cloudflare 托管的自定义子域名
#    例如 stats.your-domain.com（国内请务必绑定自定义域名，裸 *.workers.dev 常被阻断）

# 5) 部署上线
npm run deploy
```

## 3. 在客户端启用

将部署好的完整上报地址填入 `src/telemetry.py` 的 `TELEMETRY_ENDPOINT`，例如：

```python
TELEMETRY_ENDPOINT = "https://stats.your-domain.com/v1/ping"
```

本地测试也可直接用环境变量覆盖，无需改代码：

```powershell
$env:OK_BD2_TELEMETRY_URL = "https://stats.your-domain.com/v1/ping"
$env:OK_BD2_TELEMETRY_FORCE = "1"
```

## 4. 查看数据看板

浏览器打开 `https://stats.your-domain.com/dashboard`，输入刚才设置的 `ADMIN_TOKEN` 即可查看：
- DAU / WAU / MAU 与累计去重用户
- 各版本累计触达、窗口活跃、当前停留、**存量热更新**、安装包升级、老用户迁移、全新安装、版本回退数
- `prev_ver -> ver` 升级路径矩阵与每日趋势
