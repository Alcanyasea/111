# 编辑者直接发布基建方案实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让绑定到 A 账号的 Telegram 编辑者上传基建 JSON 后直接安全写入 A，同时保留所有者的草稿发布流程。

**Architecture:** `BaseScheduleBot` 按角色分流。编辑者从 `RolePolicy` 取得绑定账号并调用现有 `build_schedule_patch`，随后通过现有 `gui.config.save` 原子保存；所有者继续创建草稿。编辑者请求永远忽略 caption 中的账号 ID。

**Tech Stack:** Python 标准库、现有 Telegram Bot API 封装、项目 `gui/config.py` 保存机制、unittest。

**Spec:** `docs/superpowers/specs/2026-11-04-editor-direct-publish-design.md`

## Global Constraints

- 不启动或停止 MAA，不读取或写入密码、token、路径等敏感字段。
- 测试使用临时目录和内存配置，不碰真实 `config.json` 或 MAA 数据。
- 编辑者只能写入绑定账号 A，不能通过 caption 改变目标账号。

## Review Focus

- 编辑者上传时 caption 指向其他账号：必须仍只更新绑定账号。
- 编辑者写入后其他账号：必须保持不变。
- 编辑者写入失败：不得产生已发布草稿或半写入配置。

### Task 1: 编辑者直接写入

**Files:**
- Modify: `tests/test_telegram_base_schedule.py`
- Modify: `remote_control/telegram_bot.py`
- Modify: `docs/telegram-base-schedule.md`

**Interfaces:**
- Consumes: `RolePolicy.account_for`, `build_schedule_patch`, `gui.config.save`.
- Produces: 编辑者上传消息直接写入绑定账号并返回成功消息；所有者路径不变。

- [ ] **Step 1: 写失败测试**
  将编辑者上传测试改为断言上传后绑定账号的 `base_schedule` 已更新、草稿文件为空，并增加 caption 指向其他账号时其他账号不变的断言。
- [ ] **Step 2: 运行专项测试确认失败**
  `python -m unittest tests.test_telegram_base_schedule.TelegramBaseScheduleTests.test_editor_uploads_bound_account_directly`
- [ ] **Step 3: 实现最小改动**
  为 `RolePolicy` 提供绑定账号查询；编辑者上传忽略 caption，保存仅绑定账号的 patch；所有者继续草稿流程。
- [ ] **Step 4: 更新文档**
  说明编辑者上传即写入 A，所有者仍可发布草稿。
- [ ] **Step 5: 验证**
  `python -m unittest tests.test_telegram_base_schedule`

### Task 2: 全量回归

- [ ] **Step 1:** `python -m unittest discover -s tests`
- [ ] **Step 2:** `python -m py_compile remote_control/telegram_base_schedule.py remote_control/telegram_bot.py`
