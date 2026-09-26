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
- `POST /api/allocations/{id}/transit`、`delay`：冷链转运（进入转运时生成随机封签码）和延误上报。
- `POST /api/allocations/{id}/handoff`、`handoff-accept`：来源医院发起、接收医院确认；双方都必须在请求体上报 `seal_code` 并与转运封签核验一致。
- `POST /api/allocations/{id}/seal-supplement`：已有转运中分配缺封签时，由分配员补录随机封签。
- `POST /api/allocations/{id}/implant`：确认植入。
- `GET /api/allocations/{id}/audit`、`GET /api/state`：完整审计和权限视图（`seal_board` 给出缺封签待补与核验差异清单）。

## 封签交接

- 转运开始（`transit`）后系统生成形如 `SEAL-XXXXXXXXXX` 的随机一次性封签码，随分配返回（`seal_code`）。
- 来源医院 `handoff` 与接收医院 `handoff-accept` 都必须上报同一封签：码不一致返回 `seal_mismatch`，封签在其他分配已使用返回 `seal_duplicate`；交接不得完成，原分配继续停在 `in_transit`。
- 双方上报的封签、医院、人员、核验结论与时刻写入 `seal_verifications`，拒绝记录同样留痕；分配视图的 `seal` 字段汇总双方最新结论（`matched/mismatch/duplicate/expired`）。
- 器官过期后的核验只产生拒绝记录（结论 `expired`），分配落为 `expired`，绝不记为 `handed_off`。
- 判定规则集中在 `seals.py`（纯函数），持久化在 `app.py`，页面交互在 `static/index.html`，三者分开维护。


## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 主要局限

血型兼容与评分是演示规则，不包含 HLA 分型、器官大小、病程、儿科差异和真实移植网络规则。医院身份使用请求头模拟，SQLite 环境适合原型，不处理跨机构身份信任、远程患者隐私协议和真实冷链设备接入。
