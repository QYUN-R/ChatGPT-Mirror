# 商业套餐部署与数据库维护

## 生产配置

生产环境使用以下核心开关：

```text
BILLING_ENABLED=true
BILLING_ENFORCE_SUBSCRIPTION=false
BILLING_MOCK_PAYMENTS=false
PAYMENT_PROVIDER=manual
DATABASE_ENGINE=postgres
PAYMENT_CALLBACK_LOCK_ENABLED=true
PAYMENT_CALLBACK_MAX_AGE_SECONDS=900
PAYMENT_CALLBACK_MAX_FUTURE_SKEW_SECONDS=300
```

`BILLING_ENFORCE_SUBSCRIPTION=false` 用于兼容已有用户。确认旧用户迁移完成后，才评估是否改为强制套餐。

## 在线支付开关

生产环境在尚未接入支付渠道时必须保持 `PAYMENT_PROVIDER=manual`。此时普通用户不能创建占用号池席位的待支付订单，只有管理员可以通过订阅后台手动开通或续期。

本地开发可同时设置 `BILLING_MOCK_PAYMENTS=true` 和 `PAYMENT_PROVIDER=mock` 来测试完整下单链路。真实支付适配器接入并完成回调验签后，再将 `PAYMENT_PROVIDER` 切换为对应渠道代码；不要在生产环境启用 mock 支付。

## 前端构建与发布

`gateway/static` 是构建产物，不提交到 Git。每次更新包含 `frontend/` 的版本后，必须在启动服务前执行：

```bash
./scripts/build-frontend.sh
```

该脚本使用独立的 Node 20 Docker 容器执行 `npm ci` 和生产构建，不要求宿主机安装 Node.js。构建产物会写入 `gateway/static`，并由 gateway 通过只读挂载提供。构建完成后刷新管理后台；如果浏览器仍使用旧入口文件，执行一次强制刷新。

## SQLite 迁移到 PostgreSQL

1. 对 SQLite 执行一致性备份并保存 `.env`、Compose 配置和当前 Git 版本。
2. 启动 PostgreSQL 16：

```bash
docker compose -f docker-compose.yml -f docker-compose.commercial.yml \
  --profile postgres up -d postgres
```

3. 在 PostgreSQL 创建完整表结构：

```bash
docker compose -f docker-compose.yml -f docker-compose.commercial.yml \
  --profile postgres run --rm \
  -e DATABASE_ENGINE=postgres \
  django python manage.py migrate --noinput
```

4. 短暂停止会写入业务数据库的服务，冻结 SQLite 数据。
5. 执行带逐模型数量和内容摘要校验的迁移：

```bash
docker compose -f docker-compose.yml -f docker-compose.commercial.yml \
  --profile postgres run --rm \
  -e DATABASE_ENGINE=postgres \
  -e MIGRATION_SOURCE_SQLITE_PATH=/app/backend/db/db.sqlite3 \
  django python manage.py migrate_sqlite_data
```

6. 将 `.env` 的 `DATABASE_ENGINE` 改为 `postgres`，再启动完整服务。
7. 验证管理员登录、注册、套餐、订单、通知和 ChatGPT 登录链路。

迁移命令在任一模型数量或内容摘要不一致时会整体回滚 PostgreSQL 写入。

## 加密备份

手动创建备份：

```bash
./scripts/run-postgres-backup.sh
```

备份使用 AES-256-CBC 与 PBKDF2 加密，保存到：

```text
backups/postgres/daily
backups/postgres/weekly
```

日备份保留至少 14 天，周备份保留至少 56 天。

安装定时任务：

```bash
./scripts/install-postgres-maintenance-cron.sh
```

默认每天 03:30 备份，每周日 04:30 在临时 PostgreSQL 16 实例执行恢复演练。

## 恢复演练

手动执行最新备份恢复测试：

```bash
./scripts/run-postgres-restore-test.sh
```

测试使用独立临时数据库容器，不会清理或覆盖生产 PostgreSQL。

## 回滚

如果 PostgreSQL 切换后出现异常：

1. 停止 gateway、Django、Worker 和 Beat。
2. 将 `.env` 的 `DATABASE_ENGINE` 改回 `sqlite`。
3. 恢复迁移前的 `backend/db/db.sqlite3`。
4. 使用原 Compose 配置重新启动服务。
5. 验证登录和旧用户 ChatGPT 链路后再对外恢复。

真实支付启用前，必须再次执行支付回调验签、金额、币种、事件时间、事件类型、事件 ID 和渠道流水号重复测试。
