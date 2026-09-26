# 器官分配与转运协调系统

Python 标准库独立项目。系统按器官类型、血型、地域、医疗匹配、紧急程度和等待时间排序候选患者，并管理提出、接受、转运、交接、植入或撤回流程。器官过期后所有继续流转操作都会被阻止，全部状态变化写入审计记录。

## 运行

```bash
python3 app.py --db organ_allocation.db
```

默认监听 `127.0.0.1:8203`，首页 `/`，健康检查 `/health`。

身份头：`X-User-Id`、`X-Role`。角色为 `viewer`、`hospital`、`coordinator`、`allocation_officer`、`auditor`；医院角色还需 `X-Hospital`。

## 主要接口

- `POST /api/donors`、`POST /api/candidates`：登记器官与候选患者。
- `GET /api/donors/{id}/ranking`：查看兼容候选排序。
- `POST /api/allocations`：提出唯一分配。
- `POST /api/allocations/{id}/accept`、`withdraw`：医院确认或撤回。
- `POST /api/allocations/{id}/transit`、`delay`：冷链转运和延误上报；转运开始时生成随机封签码。
- `POST /api/allocations/{id}/handoff`、`handoff-accept`：来源医院发起、接收医院确认，双方必须上报同一 `seal_code`。
- `POST /api/allocations/{id}/seal`：为缺封签的在途分配补发封签（仅分配员，已有封签不可替换）。
- `POST /api/allocations/{id}/implant`：确认植入。
- `GET /api/allocations/{id}/audit`、`GET /api/state`：完整审计和权限视图；协调角色的 state 含 `seal_issues` 差异列表。

## 封签核验

- 转运开始自动生成 8 位随机封签码，与仍在流转的封签不重复。
- 发起交接与确认交接都核验同一封签：不一致（`seal_mismatch`）、撞上其他在途分配的封签（`seal_duplicate`，疑似容器调换）或本单缺封签（`seal_missing`）时交接不得完成，分配保持 `in_transit`；双方上报的封签与时刻保存在 `handoffs` 与 `seal_events`。
- 器官过期后的核验只留 `expired` 拒绝记录，分配转入 `expired`，不会记为已交接。
- 协调台展示封签差异；历史在途分配缺封签时显示“待补”，补发后方可交接。
- 判定（`seal_policy.py`）、记录（`seal_log.py`）与页面交互（`static/index.html`）分层维护。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 主要局限

血型兼容与评分是演示规则，不包含 HLA 分型、器官大小、病程、儿科差异和真实移植网络规则。医院身份使用请求头模拟，SQLite 环境适合原型，不处理跨机构身份信任、远程患者隐私协议和真实冷链设备接入。
