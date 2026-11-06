# 数据与配置设计

## 1. 文件位置

| 文件 | 默认路径 | 用途 |
| --- | --- | --- |
| 软件配置 | `config/factoryguard.json` | 软件设置、模型参数、国标参数和凭据 |
| 配置示例 | `config/factoryguard.example.json` | 可提交的默认模板 |
| 品牌横幅 | `config/branding/banner.svg` | 客户自定义顶部横幅，可在界面中替换 |
| 业务数据库 | `data/factoryguard.db` | 实体、险情、对话、事件与审计 |
| 媒体目录 | `events-media` | 事件截图与短视频，可由程序创建 |

可通过以下方式修改配置路径：

```powershell
python src/main.py gui --config config/factoryguard.local.json
```

或：

```powershell
$env:FACTORYGUARD_CONFIG = "config/factoryguard.local.json"
```

## 2. 配置文件结构

```json
{
  "version": 1,
  "llm": {
    "enabled": false,
    "provider": "",
    "endpoint": "",
    "model": "",
    "timeout": 35,
    "api_key": ""
  },
  "gb28181": {
    "enabled": false,
    "platform_id": "",
    "domain": "",
    "port": 5060,
    "password": ""
  },
  "branding": {
    "banner_svg": "",
    "banner_height": 82
  },
  "notifications": {
    "enabled": false,
    "timeout": 10,
    "webhook": {
      "enabled": false,
      "url": "",
      "secret": ""
    },
    "email": {
      "enabled": false,
      "host": "",
      "port": 587,
      "username": "",
      "password": "",
      "sender": "",
      "recipients": [],
      "security": "starttls"
    },
    "sms": {
      "enabled": false,
      "endpoint": "",
      "provider": "custom_http",
      "token": "",
      "sender": "",
      "template_id": "",
      "recipients": []
    }
  },
  "credentials": {}
}
```

`branding.banner_svg` 是相对于配置文件所在目录的路径；空字符串表示使用内置默认横幅。横幅高度范围为 64～160 px，SVG 文件上限为 5 MB。界面会把客户文件复制为 `branding/banner.svg`，避免原始文件移动后配置失效。

`credentials` 以凭据引用为键，保存设备账号 JSON。例如：

```json
{
  "credentials": {
    "cred-device-1": {
      "username": "admin",
      "password": "plain-secret",
      "token": ""
    }
  }
}
```

## 3. 配置写入策略

- 使用临时文件写入，再通过 `os.replace()` 原子替换；
- 写入失败不会破坏旧文件；
- 顶层必须是 JSON 对象；
- 缺失字段自动合并默认值；
- 非法 JSON 在启动时提示具体路径；
- 客户横幅只保存相对于配置文件目录的本地路径；
- SVG 装载前检查文件大小、SVG 根节点和脚本内容；
- 配置保存失败时不应用未落盘的主题变化；
- 当前版本不加密，依赖 Windows 文件系统权限保护配置。

## 4. 旧配置迁移

首次启动时，如果新配置尚无实质内容，会尝试迁移：

- 旧 `QSettings("FactoryGuard", "MerchantConsole")` 中的 LLM/GB 设置；
- SQLite `device_credential` 表中旧 DPAPI 凭据；
- 与设备实体关联的摄像头凭据。

成功写入 JSON 后，旧数据库凭据被删除，旧 MerchantConsole 注册表键被清空。迁移失败不会阻止应用启动，也不会删除旧数据。

## 5. 业务数据库

主要表：

| 表 | 内容 |
| --- | --- |
| `site`、`area` | 场所和区域层级 |
| `video_device`、`camera_device`、`video_stream` | 标准设备和码流 |
| `incident` | 面向用户的险情 |
| `incident_notification_delivery` | 险情各通知渠道的状态、尝试次数和错误信息 |
| `assistant_message` | 问询历史 |
| `event` | 设备/分析事件 |
| `stream_health` | 码流健康 |
| `device_subscription` | 设备事件订阅 |
| `audit_log` | 变更与处置审计 |

软件配置不进入上述业务表。`device_credential` 仅作为旧版本迁移兼容表保留，新产品运行后不再写入。

## 6. 备份与恢复

建议备份：

- `config/factoryguard.json`
- `config/branding/`（如果已配置客户横幅）
- `data/factoryguard.db`
- `events-media/`（如果存在事件截图或视频）

软件内的一键备份会打包 `factoryguard.json`、`branding/banner.svg`（如已配置）、在线一致的 `factoryguard.db` 快照和 `manifest.json`。恢复时会同步还原客户横幅，并在恢复前生成包含当前横幅的安全备份。事件媒体体积可能较大，仍按运维策略单独备份。

## 7. 容量假设

当前方案面向小规模单机部署：

- 4～32 路摄像头；
- 单值守用户或单班组；
- 单进程访问配置文件；
- 不适合多机并发写入或云端多租户。
