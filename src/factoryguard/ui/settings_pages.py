from __future__ import annotations

"""Dedicated settings pages for the FactoryGuard desktop application."""

import json
from typing import Any

from factoryguard.domain.permissions import ROLE_LABELS, UserRole, role_from_value
from factoryguard.inference.remote import CentralInferenceClient
from factoryguard.notifications.adapters import (
    EmailAdapter,
    SmsHttpAdapter,
    parse_phone_recipients,
    parse_recipients,
)
from factoryguard.notifications.destinations import NotificationDestination, new_destination_id
from factoryguard.notifications.providers import EMAIL_PRESETS, SMS_PRESETS, WEBHOOK_PRESETS, email_preset, sms_preset, webhook_preset

from .qt import HAS_QT


if HAS_QT:
    from .qt import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        Qt,
    )


    class SettingsPagesMixin:
        """Build the focused settings and notification pages."""

        def _settings_page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            heading = QLabel(title)
            heading.setObjectName("title")
            description = QLabel(subtitle)
            description.setObjectName("muted")
            description.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(description)
            return page, layout

        def _saved_notification_config(self) -> dict[str, Any]:
            value = self._local_settings.get("notification", {})
            return value if isinstance(value, dict) else {}

        def _saved_notification_channels(self) -> set[str]:
            saved = self._saved_notification_config()
            raw = saved.get("channels", self._base_config.notification_channels)
            if isinstance(raw, str):
                raw = (raw,)
            try:
                return {str(value).strip().lower() for value in raw if str(value).strip()}
            except TypeError:
                return set(self._base_config.notification_channels)

        def _destination_selected_id(self, kind: str) -> str | None:
            return getattr(self, f"_selected_{kind}_destination_id", None)

        def _set_destination_selected_id(self, kind: str, destination_id: str) -> None:
            setattr(self, f"_selected_{kind}_destination_id", destination_id)

        def _destination_provider_label(self, kind: str, provider: str) -> str:
            if kind == "webhook":
                return webhook_preset(provider).label
            if kind == "email":
                return email_preset(provider).label
            return sms_preset(provider).label

        def _destination_summary(self, kind: str, destination: NotificationDestination) -> tuple[str, ...]:
            settings = destination.settings
            if kind == "webhook":
                return (str(settings.get("url", "") or "未配置地址"),)
            if kind == "email":
                return (
                    str(settings.get("host", "") or "未配置 SMTP"),
                    str(settings.get("sender", "") or "未配置发件人"),
                )
            return (
                str(settings.get("endpoint", "") or "未配置网关"),
                str(len(settings.get("recipients", ()) or ())),
            )

        def _refresh_destination_table(self, kind: str) -> None:
            table = getattr(self, f"{kind}_destination_table")
            selected_id = self._destination_selected_id(kind)
            self._destination_table_refreshing = True
            table.setRowCount(0)
            for destination in self._destinations_for_kind(kind):
                row = table.rowCount()
                table.insertRow(row)
                summary = self._destination_summary(kind, destination)
                columns = (
                    destination.name,
                    self._destination_provider_label(kind, destination.provider),
                    *summary,
                    "启用" if destination.enabled else "停用",
                )
                for column, text in enumerate(columns):
                    item = QTableWidgetItem(str(text))
                    item.setData(Qt.UserRole, destination.id)
                    table.setItem(row, column, item)
            table.resizeColumnsToContents()
            if selected_id:
                for row in range(table.rowCount()):
                    item = table.item(row, 0)
                    if item is not None and item.data(Qt.UserRole) == selected_id:
                        table.selectRow(row)
                        break
            self._destination_table_refreshing = False

        def _select_destination_from_table(self, kind: str) -> None:
            if getattr(self, "_destination_table_refreshing", False):
                return
            table = getattr(self, f"{kind}_destination_table")
            selected = table.selectedItems()
            if not selected:
                return
            destination_id = str(selected[0].data(Qt.UserRole) or "")
            if not destination_id or destination_id == self._destination_selected_id(kind):
                return
            self._sync_destination_editor(kind)
            self._set_destination_selected_id(kind, destination_id)
            destination = self._destination_by_id(kind, destination_id)
            if destination is not None:
                self._load_destination_editor(kind, destination)

        def _add_destination(self, kind: str) -> None:
            self._sync_destination_editor(kind)
            provider = "custom" if kind in {"webhook", "email"} else "custom_http"
            destination = NotificationDestination(
                id=new_destination_id(kind),
                kind=kind,
                name=f"新{self._destination_provider_label(kind, provider)}",
                provider=provider,
                enabled=False,
            )
            self._destinations.append(destination)
            self._set_destination_selected_id(kind, destination.id)
            self._refresh_destination_table(kind)
            self._load_destination_editor(kind, destination)
            self.statusBar().showMessage("已新增独立通知实例，请填写配置并保存", 5000)

        def _duplicate_destination(self, kind: str) -> None:
            self._sync_destination_editor(kind)
            source = self._destination_by_id(kind, self._destination_selected_id(kind))
            if source is None:
                return
            destination = NotificationDestination(
                id=new_destination_id(kind),
                kind=kind,
                name=f"{source.name} 副本",
                provider=source.provider,
                enabled=False,
                settings=dict(source.settings),
                secrets=dict(source.secrets),
            )
            self._destinations.append(destination)
            self._set_destination_selected_id(kind, destination.id)
            self._refresh_destination_table(kind)
            self._load_destination_editor(kind, destination)
            self.statusBar().showMessage("已复制为独立通知实例", 5000)

        def _delete_destination(self, kind: str) -> None:
            destination = self._destination_by_id(kind, self._destination_selected_id(kind))
            if destination is None:
                return
            values = self._destinations_for_kind(kind)
            if len(values) <= 1:
                self.statusBar().showMessage("至少保留一个通知实例；可将其停用", 5000)
                return
            self._destinations.remove(destination)
            replacement = self._destinations_for_kind(kind)[0]
            self._set_destination_selected_id(kind, replacement.id)
            self._refresh_destination_table(kind)
            self._load_destination_editor(kind, replacement)
            self.statusBar().showMessage("通知实例已删除", 5000)

        def _sync_destination_editor(self, kind: str) -> None:
            destination = self._destination_by_id(kind, self._destination_selected_id(kind))
            if destination is None:
                return
            if kind == "webhook":
                destination.name = self.webhook_name_edit.text().strip() or destination.name
                destination.provider = str(self.webhook_provider_combo.currentData() or "custom")
                destination.enabled = self.webhook_enabled_check.isChecked()
                destination.settings["url"] = self.webhook_url_edit.text().strip()
                destination.secrets["secret"] = self.webhook_secret_edit.text()
            elif kind == "email":
                destination.name = self.email_name_edit.text().strip() or destination.name
                destination.provider = str(self.email_provider_combo.currentData() or "custom")
                destination.enabled = self.email_enabled_check.isChecked()
                destination.settings.update(
                    {
                        "host": self.email_host_edit.text().strip(),
                        "port": self.email_port_spin.value(),
                        "username": self.email_username_edit.text().strip(),
                        "sender": self.email_sender_edit.text().strip(),
                        "recipients": list(self._email_recipients_from_form()),
                        "security": str(self.email_security_combo.currentData() or "starttls"),
                    }
                )
                destination.secrets["password"] = self.email_password_edit.text()
            else:
                destination.name = self.sms_name_edit.text().strip() or destination.name
                destination.provider = str(self.sms_provider_combo.currentData() or "custom_http")
                destination.enabled = self.sms_enabled_check.isChecked() and self.sms_enabled_check.isEnabled()
                destination.settings.update(
                    {
                        "endpoint": self.sms_endpoint_edit.text().strip(),
                        "sender": self.sms_sender_edit.text().strip(),
                        "template_id": self.sms_template_edit.text().strip(),
                        "recipients": list(self._sms_recipients_from_form()),
                    }
                )
                destination.secrets["token"] = self.sms_token_edit.text()

        def _sync_all_destination_editors(self) -> None:
            self._sync_destination_editor("webhook")
            self._sync_destination_editor("email")
            self._sync_destination_editor("sms")

        def _load_destination_editor(self, kind: str, destination: NotificationDestination) -> None:
            settings = destination.settings
            if kind == "webhook":
                self.webhook_name_edit.setText(destination.name)
                self.webhook_enabled_check.setChecked(destination.enabled)
                self.webhook_provider_combo.setCurrentIndex(
                    max(0, self.webhook_provider_combo.findData(destination.provider))
                )
                self.webhook_url_edit.setText(str(settings.get("url", "") or ""))
                self.webhook_secret_edit.setText(str(destination.secrets.get("secret", "") or ""))
                self._apply_webhook_provider_preset()
            elif kind == "email":
                self.email_name_edit.setText(destination.name)
                self.email_enabled_check.setChecked(destination.enabled)
                self.email_provider_combo.setCurrentIndex(
                    max(0, self.email_provider_combo.findData(destination.provider))
                )
                self.email_host_edit.setText(str(settings.get("host", "") or ""))
                try:
                    port = int(settings.get("port", 587))
                except (TypeError, ValueError):
                    port = 587
                self.email_port_spin.setValue(max(1, min(65535, port)))
                self.email_username_edit.setText(str(settings.get("username", "") or ""))
                self.email_sender_edit.setText(str(settings.get("sender", "") or ""))
                recipients = settings.get("recipients", ())
                self.email_recipients_edit.setText(
                    recipients if isinstance(recipients, str) else ", ".join(str(value) for value in recipients)
                )
                security = str(settings.get("security", "starttls") or "starttls")
                self.email_security_combo.setCurrentIndex(
                    max(0, self.email_security_combo.findData(security))
                )
                self.email_password_edit.setText(str(destination.secrets.get("password", "") or ""))
                self._apply_email_provider_preset(False)
            else:
                self.sms_name_edit.setText(destination.name)
                self.sms_enabled_check.setChecked(destination.enabled)
                self.sms_provider_combo.setCurrentIndex(
                    max(0, self.sms_provider_combo.findData(destination.provider))
                )
                self.sms_endpoint_edit.setText(str(settings.get("endpoint", "") or ""))
                self.sms_sender_edit.setText(str(settings.get("sender", "") or ""))
                self.sms_template_edit.setText(str(settings.get("template_id", "") or ""))
                recipients = settings.get("recipients", ())
                self.sms_recipients_edit.setText(
                    recipients if isinstance(recipients, str) else ", ".join(str(value) for value in recipients)
                )
                self.sms_token_edit.setText(str(destination.secrets.get("token", "") or ""))
                self._apply_sms_provider_preset()

        def _build_notification_center_page(self) -> QWidget:
            page, layout = self._settings_page(
                "通知中心",
                "集中查看投递策略、Outbox 队列和最近一次调度结果；通道凭据在独立页面维护。",
            )

            scheduler = QGroupBox("投递调度")
            scheduler_layout = QFormLayout(scheduler)
            self.notification_interval_spin = QDoubleSpinBox()
            self.notification_interval_spin.setRange(1.0, 300.0)
            self.notification_interval_spin.setSingleStep(1.0)
            saved = self._saved_notification_config()
            try:
                saved_interval = float(saved.get("interval", self._base_config.notification_interval))
            except (TypeError, ValueError):
                saved_interval = self._base_config.notification_interval
            self.notification_interval_spin.setValue(saved_interval)

            self.notification_batch_spin = QSpinBox()
            self.notification_batch_spin.setRange(1, 1000)
            try:
                saved_batch = int(saved.get("batch", self._base_config.notification_batch))
            except (TypeError, ValueError):
                saved_batch = self._base_config.notification_batch
            self.notification_batch_spin.setValue(saved_batch)

            self.notification_status = QLabel("监控启动后自动调度")
            self.notification_status.setObjectName("muted")
            save_settings = QPushButton("保存通知调度")
            save_settings.setObjectName("primary")
            save_settings.clicked.connect(self._save_notification_settings)
            scheduler_layout.addRow("轮询间隔（秒）", self.notification_interval_spin)
            scheduler_layout.addRow("单批数量", self.notification_batch_spin)
            scheduler_layout.addRow("当前状态", self.notification_status)
            scheduler_layout.addRow("", save_settings)
            channel_hint = QLabel(
                "启用或停用通道，请分别前往“Webhook 通道”“邮件 SMTP”和“短信通知服务”页面。"
            )
            channel_hint.setWordWrap(True)
            channel_hint.setObjectName("muted")
            scheduler_layout.addRow("通道开关", channel_hint)
            layout.addWidget(scheduler)

            queue = QGroupBox("Outbox 投递队列")
            queue_layout = QVBoxLayout(queue)
            self.notification_queue_summary = QLabel("等待读取通知队列")
            self.notification_queue_summary.setObjectName("muted")
            queue_layout.addWidget(self.notification_queue_summary)
            self.notification_outbox_table = QTableWidget(0, 8)
            self.notification_outbox_table.setHorizontalHeaderLabels(
                ["状态", "通道", "事件", "级别", "尝试", "下次重试", "发送时间", "最后错误"]
            )
            self.notification_outbox_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.notification_outbox_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.notification_outbox_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.notification_outbox_table.setAlternatingRowColors(True)
            self.notification_outbox_table.horizontalHeader().setStretchLastSection(True)
            self.notification_outbox_table.itemSelectionChanged.connect(self._on_notification_selected)
            queue_layout.addWidget(self.notification_outbox_table, 1)

            actions = QHBoxLayout()
            self.refresh_notification_button = QPushButton("刷新队列")
            self.send_notification_button = QPushButton("立即发送一批")
            self.send_notification_button.setObjectName("primary")
            self.requeue_notification_button = QPushButton("重新入队失败项")
            self.requeue_notification_button.setEnabled(False)
            self.refresh_notification_button.clicked.connect(self._refresh_notification_queue)
            self.send_notification_button.clicked.connect(self._dispatch_notifications_once)
            self.requeue_notification_button.clicked.connect(self._requeue_selected_notification)
            actions.addWidget(self.refresh_notification_button)
            actions.addWidget(self.send_notification_button)
            actions.addWidget(self.requeue_notification_button)
            actions.addStretch(1)
            queue_layout.addLayout(actions)
            layout.addWidget(queue, 1)
            return page

        def _build_general_settings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "常规设置",
                "维护界面外观、审计操作身份和本地运行策略。",
            )
            appearance = QGroupBox("外观")
            appearance_layout = QFormLayout(appearance)
            appearance_layout.addRow("当前主题", QLabel("使用窗口右上角的主题选择器切换浅色、深色或海蓝主题"))
            appearance_layout.addRow("布局", QLabel("左侧按工作台、网络与设备、系统三类组织页面"))
            layout.addWidget(appearance)

            identity = QGroupBox("操作身份")
            identity_layout = QFormLayout(identity)
            saved_operator = str(self._local_settings.get("operator", "ui") or "ui")
            self.operator_name_edit = QLineEdit(saved_operator)
            self.operator_name_edit.setPlaceholderText("例如：值守甲、管理员或 ui")
            saved_role = role_from_value(self._local_settings.get("role", UserRole.ADMIN.value))
            self.operator_role_combo = QComboBox()
            for role in UserRole:
                self.operator_role_combo.addItem(ROLE_LABELS[role], role.value)
            self.operator_role_combo.setCurrentIndex(
                max(0, self.operator_role_combo.findData(saved_role.value))
            )
            save_operator_button = QPushButton("保存操作身份")
            save_operator_button.setObjectName("primary")
            save_operator_button.clicked.connect(self._save_operator_settings)
            identity_layout.addRow("当前操作人", self.operator_name_edit)
            identity_layout.addRow("当前角色", self.operator_role_combo)
            identity_layout.addRow("", save_operator_button)
            identity_hint = QLabel("操作人会写入事件处置、规则、布撤防和配置变更审计。")
            identity_hint.setWordWrap(True)
            identity_hint.setObjectName("muted")
            identity_layout.addRow("", identity_hint)
            layout.addWidget(identity)

            runtime = QGroupBox("运行策略")
            runtime_layout = QFormLayout(runtime)
            runtime_layout.addRow("推理引擎", QLabel("ONNX Runtime · CPU"))
            runtime_layout.addRow("视频协议", QLabel("RTSP over TCP"))
            storage_summary = QLabel("SQLite（WAL）+ 可靠通知队列（Outbox）")
            storage_summary.setToolTip("事件与通知任务先落盘；网络恢复后由后台调度器继续发送。")
            runtime_layout.addRow("事件存储", storage_summary)
            layout.addWidget(runtime)
            layout.addStretch(1)
            return page

        def _build_inference_settings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "集中推理",
                "一台中心主机统一运行模型，多个值守终端只取流、显示结果和处置事件，降低终端硬件与运维成本。",
            )
            saved = self._local_settings.get("inference", {})
            if not isinstance(saved, dict):
                saved = {}

            service = QGroupBox("推理服务")
            service_layout = QFormLayout(service)
            self.inference_mode_combo = QComboBox()
            self.inference_mode_combo.addItem("集中推理服务（推荐）", "central")
            self.inference_mode_combo.addItem("本地 CPU 推理（兼容模式）", "local")
            saved_mode = str(saved.get("mode", self._base_config.inference_mode) or "central")
            self.inference_mode_combo.setCurrentIndex(
                max(0, self.inference_mode_combo.findData(saved_mode))
            )
            self.inference_url_edit = QLineEdit(
                str(saved.get("url", self._base_config.central_inference_url) or "")
            )
            self.inference_url_edit.setPlaceholderText("http://192.168.1.10:8765")
            self.inference_api_key_edit = QLineEdit(self._base_config.central_inference_api_key)
            self.inference_api_key_edit.setEchoMode(QLineEdit.Password)
            self.inference_api_key_edit.setPlaceholderText("仅保留在当前进程")
            self.inference_timeout_spin = QDoubleSpinBox()
            self.inference_timeout_spin.setRange(0.5, 120.0)
            self.inference_timeout_spin.setSingleStep(0.5)
            self.inference_timeout_spin.setValue(float(saved.get("timeout", self._base_config.central_inference_timeout)))
            self.inference_status_label = QLabel("等待测试中心推理连接")
            self.inference_status_label.setWordWrap(True)
            self.inference_status_label.setObjectName("muted")
            service_layout.addRow("运行模式", self.inference_mode_combo)
            service_layout.addRow("中心服务地址", self.inference_url_edit)
            service_layout.addRow("访问令牌", self.inference_api_key_edit)
            service_layout.addRow("请求超时（秒）", self.inference_timeout_spin)
            service_layout.addRow("当前状态", self.inference_status_label)
            action_row = QHBoxLayout()
            test_connection = QPushButton("测试连接")
            save_service = QPushButton("保存推理设置")
            save_service.setObjectName("primary")
            test_connection.clicked.connect(self._test_central_inference_connection)
            save_service.clicked.connect(self._save_inference_settings)
            action_row.addWidget(test_connection)
            action_row.addWidget(save_service)
            action_row.addStretch(1)
            service_layout.addRow("", action_row)
            layout.addWidget(service)

            guidance = QGroupBox("小微企业部署建议")
            guidance_layout = QVBoxLayout(guidance)
            costs = QLabel(
                "推荐一台中心电脑运行推理服务、事件库和通知调度；摄像机继续由 NVR 录像。"
                "终端只安装控制台，不再为每台电脑配置独立模型和 GPU。"
                "4-8 路低帧率子码流可先使用 CPU；路数增加时再升级单张消费级 GPU。"
            )
            costs.setWordWrap(True)
            costs.setObjectName("muted")
            guidance_layout.addWidget(costs)
            layout.addWidget(guidance)
            layout.addStretch(1)
            return page

        def _save_inference_settings(self) -> None:
            mode = str(self.inference_mode_combo.currentData() or "central")
            url = self.inference_url_edit.text().strip()
            if not url.lower().startswith(("http://", "https://")):
                self.statusBar().showMessage("中心推理地址必须使用 HTTP 或 HTTPS", 6000)
                return
            self._local_settings["inference"] = {
                "mode": mode,
                "url": url,
                "timeout": self.inference_timeout_spin.value(),
            }
            try:
                self._local_settings_path.parent.mkdir(parents=True, exist_ok=True)
                self._local_settings_path.write_text(
                    json.dumps(self._local_settings, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.statusBar().showMessage(f"推理设置保存失败：{exc}", 6000)
                return
            self.statusBar().showMessage("推理设置已保存（访问令牌仅保留在当前进程）", 5000)

        def _test_central_inference_connection(self) -> None:
            if str(self.inference_mode_combo.currentData() or "central") == "local":
                self.inference_status_label.setText("当前为本地 CPU 兼容模式，不需要中心服务")
                return
            try:
                client = CentralInferenceClient(
                    self.inference_url_edit.text(),
                    api_key=self.inference_api_key_edit.text(),
                    timeout=self.inference_timeout_spin.value(),
                )
                health = client.health_check()
            except ValueError as exc:
                self.inference_status_label.setText(f"配置无效：{exc}")
                return
            self.inference_status_label.setText(
                f"{health.state} · {health.provider} · {health.detail or '中心推理服务已响应'}"
            )

        def _build_webhook_settings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "Webhook 通道",
                "可同时添加多个独立 Webhook 实例；每个实例分别生成 Outbox 投递任务。",
            )

            instances = QGroupBox("Webhook 实例")
            instances_layout = QVBoxLayout(instances)
            self.webhook_destination_table = QTableWidget(0, 4)
            self.webhook_destination_table.setHorizontalHeaderLabels(["名称", "服务商", "地址", "状态"])
            self.webhook_destination_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.webhook_destination_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.webhook_destination_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.webhook_destination_table.setAlternatingRowColors(True)
            self.webhook_destination_table.horizontalHeader().setStretchLastSection(True)
            self.webhook_destination_table.itemSelectionChanged.connect(
                lambda: self._select_destination_from_table("webhook")
            )
            instances_layout.addWidget(self.webhook_destination_table)
            instance_actions = QHBoxLayout()
            add_webhook = QPushButton("新增实例")
            duplicate_webhook = QPushButton("复制实例")
            delete_webhook = QPushButton("删除实例")
            add_webhook.clicked.connect(lambda: self._add_destination("webhook"))
            duplicate_webhook.clicked.connect(lambda: self._duplicate_destination("webhook"))
            delete_webhook.clicked.connect(lambda: self._delete_destination("webhook"))
            instance_actions.addWidget(add_webhook)
            instance_actions.addWidget(duplicate_webhook)
            instance_actions.addWidget(delete_webhook)
            instance_actions.addStretch(1)
            instances_layout.addLayout(instance_actions)
            layout.addWidget(instances)

            channel = QGroupBox("通道状态")
            channel_layout = QFormLayout(channel)
            self.webhook_name_edit = QLineEdit()
            self.webhook_name_edit.setPlaceholderText("例如：东门值守群")
            self.webhook_enabled_check = QCheckBox("启用 Webhook 投递")
            self.webhook_enabled_check.setChecked(False)
            channel_layout.addRow("实例名称", self.webhook_name_edit)
            channel_layout.addRow("启用", self.webhook_enabled_check)
            layout.addWidget(channel)

            provider_group = QGroupBox("服务商预设")
            provider_layout = QFormLayout(provider_group)
            self.webhook_provider_combo = QComboBox()
            for preset in WEBHOOK_PRESETS:
                self.webhook_provider_combo.addItem(preset.label, preset.key)
            self.webhook_provider_combo.currentIndexChanged.connect(self._apply_webhook_provider_preset)
            self.webhook_provider_hint = QLabel()
            self.webhook_provider_hint.setWordWrap(True)
            self.webhook_provider_hint.setObjectName("muted")
            provider_layout.addRow("服务商", self.webhook_provider_combo)
            provider_layout.addRow("说明", self.webhook_provider_hint)
            layout.addWidget(provider_group)

            connection = QGroupBox("连接配置")
            connection_layout = QFormLayout(connection)
            self.webhook_url_edit = QLineEdit()
            self.webhook_url_edit.setPlaceholderText("https://example.com/factoryguard/webhook")
            self.webhook_secret_edit = QLineEdit()
            self.webhook_secret_edit.setEchoMode(QLineEdit.Password)
            self.webhook_secret_edit.setPlaceholderText("留空则使用启动参数或环境变量")
            connection_layout.addRow("回调地址", self.webhook_url_edit)
            connection_layout.addRow("签名密钥", self.webhook_secret_edit)
            save_webhook = QPushButton("保存 Webhook 配置")
            save_webhook.setObjectName("primary")
            save_webhook.clicked.connect(self._save_notification_settings)
            connection_layout.addRow("", save_webhook)
            hint = QLabel(
                "仅接受 HTTPS；请求会携带 X-FactoryGuard-Event-Id 和 "
                "X-FactoryGuard-Dedup-Key 以便接收端幂等处理。"
            )
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            connection_layout.addRow("投递约定", hint)
            layout.addWidget(connection)
            self._refresh_destination_table("webhook")
            selected = self._destination_by_id("webhook", self._destination_selected_id("webhook"))
            if selected is not None:
                self._load_destination_editor("webhook", selected)
            layout.addStretch(1)
            return page

        def _apply_webhook_provider_preset(self) -> None:
            preset = webhook_preset(str(self.webhook_provider_combo.currentData() or "custom"))
            self.webhook_url_edit.setPlaceholderText(preset.url_hint)
            self.webhook_secret_edit.setPlaceholderText(preset.secret_label)
            self.webhook_provider_hint.setText(preset.detail)

        def _build_email_settings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "邮件 SMTP",
                "可同时维护多个独立邮箱账号；每个账号分别生成 Outbox 投递任务。",
            )

            instances = QGroupBox("邮箱实例")
            instances_layout = QVBoxLayout(instances)
            self.email_destination_table = QTableWidget(0, 5)
            self.email_destination_table.setHorizontalHeaderLabels(
                ["名称", "服务商", "SMTP", "发件人", "状态"]
            )
            self.email_destination_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.email_destination_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.email_destination_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.email_destination_table.setAlternatingRowColors(True)
            self.email_destination_table.horizontalHeader().setStretchLastSection(True)
            self.email_destination_table.itemSelectionChanged.connect(
                lambda: self._select_destination_from_table("email")
            )
            instances_layout.addWidget(self.email_destination_table)
            instance_actions = QHBoxLayout()
            add_email = QPushButton("新增实例")
            duplicate_email = QPushButton("复制实例")
            delete_email = QPushButton("删除实例")
            add_email.clicked.connect(lambda: self._add_destination("email"))
            duplicate_email.clicked.connect(lambda: self._duplicate_destination("email"))
            delete_email.clicked.connect(lambda: self._delete_destination("email"))
            instance_actions.addWidget(add_email)
            instance_actions.addWidget(duplicate_email)
            instance_actions.addWidget(delete_email)
            instance_actions.addStretch(1)
            instances_layout.addLayout(instance_actions)
            layout.addWidget(instances)

            channel = QGroupBox("通道状态")
            channel_layout = QFormLayout(channel)
            self.email_name_edit = QLineEdit()
            self.email_name_edit.setPlaceholderText("例如：值班邮箱")
            self.email_enabled_check = QCheckBox("启用邮件 SMTP 投递")
            self.email_enabled_check.setChecked(False)
            channel_layout.addRow("实例名称", self.email_name_edit)
            channel_layout.addRow("启用", self.email_enabled_check)
            layout.addWidget(channel)

            provider_group = QGroupBox("邮箱服务商预设")
            provider_layout = QFormLayout(provider_group)
            self.email_provider_combo = QComboBox()
            for preset in EMAIL_PRESETS:
                self.email_provider_combo.addItem(preset.label, preset.key)
            self.email_provider_hint = QLabel()
            self.email_provider_hint.setWordWrap(True)
            self.email_provider_hint.setObjectName("muted")
            provider_layout.addRow("服务商", self.email_provider_combo)
            provider_layout.addRow("预设说明", self.email_provider_hint)
            layout.addWidget(provider_group)

            connection = QGroupBox("SMTP 连接")
            connection_layout = QFormLayout(connection)
            self.email_host_edit = QLineEdit()
            self.email_host_edit.setPlaceholderText("smtp.example.com")
            self.email_port_spin = QSpinBox()
            self.email_port_spin.setRange(1, 65535)
            self.email_port_spin.setValue(587)
            self.email_username_edit = QLineEdit()
            self.email_username_edit.setPlaceholderText("登录账号，可留空")
            self.email_password_edit = QLineEdit()
            self.email_password_edit.setEchoMode(QLineEdit.Password)
            self.email_password_edit.setPlaceholderText("SMTP 密码或应用授权码，仅保留在当前进程")
            self.email_sender_edit = QLineEdit()
            self.email_sender_edit.setPlaceholderText("factoryguard@example.com")
            self.email_recipients_edit = QLineEdit()
            self.email_recipients_edit.setPlaceholderText(
                "operator@example.com, security@example.com（逗号、分号或换行分隔）"
            )
            self.email_security_combo = QComboBox()
            self.email_security_combo.addItem("STARTTLS（常用 587）", "starttls")
            self.email_security_combo.addItem("SSL/TLS（常用 465）", "ssl")
            self.email_security_combo.addItem("明文 SMTP（不推荐）", "none")
            self.email_security_combo.setCurrentIndex(0)
            self.email_provider_combo.currentIndexChanged.connect(
                lambda _index: self._apply_email_provider_preset(True)
            )
            connection_layout.addRow("SMTP 主机", self.email_host_edit)
            connection_layout.addRow("SMTP 端口", self.email_port_spin)
            connection_layout.addRow("SMTP 账号", self.email_username_edit)
            connection_layout.addRow("SMTP 密码", self.email_password_edit)
            connection_layout.addRow("发件人", self.email_sender_edit)
            connection_layout.addRow("收件人", self.email_recipients_edit)
            connection_layout.addRow("邮件加密", self.email_security_combo)
            action_row = QHBoxLayout()
            validate_email = QPushButton("校验配置")
            save_email = QPushButton("保存邮件配置")
            save_email.setObjectName("primary")
            validate_email.clicked.connect(self._validate_email_configuration)
            save_email.clicked.connect(self._save_notification_settings)
            action_row.addWidget(validate_email)
            action_row.addWidget(save_email)
            action_row.addStretch(1)
            connection_layout.addRow("", action_row)
            hint = QLabel("校验只检查地址和参数格式，不会连接 SMTP 服务器或发送测试邮件。")
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            connection_layout.addRow("", hint)
            layout.addWidget(connection)
            self._refresh_destination_table("email")
            selected = self._destination_by_id("email", self._destination_selected_id("email"))
            if selected is not None:
                self._load_destination_editor("email", selected)
            layout.addStretch(1)
            return page

        def _apply_email_provider_preset(self, apply_values: bool) -> None:
            preset = email_preset(str(self.email_provider_combo.currentData() or "custom"))
            self.email_provider_hint.setText(preset.detail)
            if not apply_values or preset.key == "custom":
                return
            self.email_host_edit.setText(preset.host)
            self.email_port_spin.setValue(preset.port)
            security_index = self.email_security_combo.findData(preset.security)
            self.email_security_combo.setCurrentIndex(max(0, security_index))

        def _validate_email_configuration(self) -> None:
            try:
                recipients = parse_recipients(self.email_recipients_edit.text())
                use_ssl, starttls = self._email_security_settings()
                EmailAdapter(
                    host=self.email_host_edit.text(),
                    port=self.email_port_spin.value(),
                    username=self.email_username_edit.text(),
                    password=self.email_password_edit.text(),
                    sender=self.email_sender_edit.text(),
                    recipients=recipients,
                    use_ssl=use_ssl,
                    starttls=starttls,
                )
            except ValueError as exc:
                self.statusBar().showMessage(f"邮件配置无效：{exc}", 7000)
                return
            self.statusBar().showMessage(
                f"邮件配置格式有效 · {len(recipients)} 个收件人（尚未连接 SMTP 服务器）",
                7000,
            )

        def _build_sms_settings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "短信通知服务",
                "可同时维护多个独立短信网关实例；每个实例分别生成 Outbox 投递任务。",
            )

            instances = QGroupBox("短信实例")
            instances_layout = QVBoxLayout(instances)
            self.sms_destination_table = QTableWidget(0, 5)
            self.sms_destination_table.setHorizontalHeaderLabels(
                ["名称", "服务商", "网关", "号码数", "状态"]
            )
            self.sms_destination_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.sms_destination_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.sms_destination_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.sms_destination_table.setAlternatingRowColors(True)
            self.sms_destination_table.horizontalHeader().setStretchLastSection(True)
            self.sms_destination_table.itemSelectionChanged.connect(
                lambda: self._select_destination_from_table("sms")
            )
            instances_layout.addWidget(self.sms_destination_table)
            instance_actions = QHBoxLayout()
            add_sms = QPushButton("新增实例")
            duplicate_sms = QPushButton("复制实例")
            delete_sms = QPushButton("删除实例")
            add_sms.clicked.connect(lambda: self._add_destination("sms"))
            duplicate_sms.clicked.connect(lambda: self._duplicate_destination("sms"))
            delete_sms.clicked.connect(lambda: self._delete_destination("sms"))
            instance_actions.addWidget(add_sms)
            instance_actions.addWidget(duplicate_sms)
            instance_actions.addWidget(delete_sms)
            instance_actions.addStretch(1)
            instances_layout.addLayout(instance_actions)
            layout.addWidget(instances)

            channel = QGroupBox("通道状态")
            channel_layout = QFormLayout(channel)
            self.sms_name_edit = QLineEdit()
            self.sms_name_edit.setPlaceholderText("例如：夜间值班短信")
            self.sms_enabled_check = QCheckBox("启用短信投递")
            self.sms_enabled_check.setChecked(False)
            channel_layout.addRow("实例名称", self.sms_name_edit)
            channel_layout.addRow("启用", self.sms_enabled_check)
            layout.addWidget(channel)

            provider_group = QGroupBox("短信服务商")
            provider_layout = QFormLayout(provider_group)
            self.sms_provider_combo = QComboBox()
            for preset in SMS_PRESETS:
                self.sms_provider_combo.addItem(preset.label, preset.key)
            self.sms_provider_hint = QLabel()
            self.sms_provider_hint.setWordWrap(True)
            self.sms_provider_hint.setObjectName("muted")
            provider_layout.addRow("服务商", self.sms_provider_combo)
            provider_layout.addRow("说明", self.sms_provider_hint)
            layout.addWidget(provider_group)

            connection = QGroupBox("HTTP 网关配置")
            connection_layout = QFormLayout(connection)
            self.sms_endpoint_edit = QLineEdit()
            self.sms_token_edit = QLineEdit()
            self.sms_token_edit.setEchoMode(QLineEdit.Password)
            self.sms_token_edit.setPlaceholderText("Bearer Token，仅保留在当前进程")
            self.sms_sender_edit = QLineEdit()
            self.sms_sender_edit.setPlaceholderText("短信签名或发送方标识")
            self.sms_template_edit = QLineEdit()
            self.sms_template_edit.setPlaceholderText("模板 ID，可留空")
            self.sms_recipients_edit = QLineEdit()
            self.sms_recipients_edit.setPlaceholderText("13800138000, +8613900139000")
            connection_layout.addRow("网关地址", self.sms_endpoint_edit)
            connection_layout.addRow("访问令牌", self.sms_token_edit)
            connection_layout.addRow("短信签名", self.sms_sender_edit)
            connection_layout.addRow("模板 ID", self.sms_template_edit)
            connection_layout.addRow("接收号码", self.sms_recipients_edit)
            action_row = QHBoxLayout()
            validate_sms = QPushButton("校验配置")
            save_sms = QPushButton("保存短信配置")
            save_sms.setObjectName("primary")
            validate_sms.clicked.connect(self._validate_sms_configuration)
            save_sms.clicked.connect(self._save_notification_settings)
            action_row.addWidget(validate_sms)
            action_row.addWidget(save_sms)
            action_row.addStretch(1)
            connection_layout.addRow("", action_row)
            hint = QLabel(
                "通用网关请求体包含 provider、to、sender、template_id、message 和 event；"
                "校验不会实际发送短信。"
            )
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            connection_layout.addRow("协议约定", hint)
            layout.addWidget(connection)
            self.sms_provider_combo.currentIndexChanged.connect(self._apply_sms_provider_preset)
            self._refresh_destination_table("sms")
            selected = self._destination_by_id("sms", self._destination_selected_id("sms"))
            if selected is not None:
                self._load_destination_editor("sms", selected)
            layout.addStretch(1)
            return page

        def _apply_sms_provider_preset(self) -> None:
            preset = sms_preset(str(self.sms_provider_combo.currentData() or "custom_http"))
            self.sms_endpoint_edit.setPlaceholderText(preset.endpoint_hint)
            if preset.implemented:
                self.sms_provider_hint.setText(preset.detail)
                self.sms_enabled_check.setEnabled(True)
                return
            self.sms_provider_hint.setText(f"{preset.detail} 当前页面仅保存选择，暂不能启用投递。")
            self.sms_enabled_check.setChecked(False)
            self.sms_enabled_check.setEnabled(False)

        def _validate_sms_configuration(self) -> None:
            try:
                recipients = parse_phone_recipients(self.sms_recipients_edit.text())
                SmsHttpAdapter(
                    endpoint=self.sms_endpoint_edit.text(),
                    recipients=recipients,
                    provider=str(self.sms_provider_combo.currentData() or "custom_http"),
                    token=self.sms_token_edit.text(),
                    sender=self.sms_sender_edit.text(),
                    template_id=self.sms_template_edit.text(),
                )
            except ValueError as exc:
                self.statusBar().showMessage(f"短信配置无效：{exc}", 7000)
                return
            self.statusBar().showMessage(
                f"短信配置格式有效 · {len(recipients)} 个接收号码（尚未调用网关）",
                7000,
            )

        def _build_storage_settings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "存储与媒体",
                "管理 SQLite 数据库、配置包、备份与事件媒体保留策略。",
            )
            database_group = QGroupBox("数据库")
            database_layout = QFormLayout(database_group)
            self.storage_db_label = QLabel(self.db_edit.text().strip() or self._base_config.db)
            self.storage_db_label.setWordWrap(True)
            database_layout.addRow("当前路径", self.storage_db_label)
            open_database = QPushButton("打开数据库目录")
            open_database.clicked.connect(self._open_database)
            backup = QPushButton("备份本地数据库")
            backup.clicked.connect(self._backup_database)
            database_actions = QHBoxLayout()
            database_actions.addWidget(open_database)
            database_actions.addWidget(backup)
            database_actions.addStretch(1)
            database_layout.addRow("操作", database_actions)
            database_hint = QLabel("数据库使用 SQLite WAL 模式；备份会生成一致性快照并保留原始文件。")
            database_hint.setWordWrap(True)
            database_hint.setObjectName("muted")
            database_layout.addRow("", database_hint)
            layout.addWidget(database_group)

            configuration = QGroupBox("配置包")
            configuration_layout = QVBoxLayout(configuration)
            configuration_hint = QLabel("导出内容包含拓扑、规则、主题与脱敏后的通知配置，不包含密钥和密码。")
            configuration_hint.setWordWrap(True)
            configuration_hint.setObjectName("muted")
            configuration_layout.addWidget(configuration_hint)
            config_actions = QHBoxLayout()
            export_config = QPushButton("导出配置包")
            import_config = QPushButton("导入配置包")
            export_config.clicked.connect(self._export_configuration)
            import_config.clicked.connect(self._import_configuration)
            config_actions.addWidget(export_config)
            config_actions.addWidget(import_config)
            config_actions.addStretch(1)
            configuration_layout.addLayout(config_actions)
            layout.addWidget(configuration)

            retention = QGroupBox("媒体保留")
            retention_layout = QFormLayout(retention)
            self.retention_days_spin = QDoubleSpinBox()
            self.retention_days_spin.setRange(0.0, 3650.0)
            self.retention_days_spin.setSingleStep(1.0)
            self.retention_days_spin.setValue(30.0)
            cleanup_media = QPushButton("清理过期媒体")
            cleanup_media.clicked.connect(self._cleanup_media)
            retention_layout.addRow("保留天数", self.retention_days_spin)
            retention_layout.addRow("执行", cleanup_media)
            hint = QLabel("只清理数据库目录下的截图和片段文件，事件、通知和审计记录会保留。")
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            retention_layout.addRow("", hint)
            layout.addWidget(retention)
            layout.addStretch(1)

            def update_database_label(value: str) -> None:
                self.storage_db_label.setText(value.strip() or self._base_config.db)

            self.db_edit.textChanged.connect(update_database_label)
            return page

        def _build_health_settings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "健康诊断",
                "执行部署预检，查看数据库、磁盘、模型、媒体目录和通知配置状态。",
            )
            health = QGroupBox("运行健康")
            health_layout = QVBoxLayout(health)
            self.health_label = QLabel("等待健康检查")
            self.health_label.setWordWrap(True)
            self.health_label.setObjectName("muted")
            health_layout.addWidget(self.health_label)
            self.preflight_table = QTableWidget(0, 3)
            self.preflight_table.setHorizontalHeaderLabels(["检查项", "状态", "详情"])
            self.preflight_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.preflight_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.preflight_table.setAlternatingRowColors(True)
            self.preflight_table.horizontalHeader().setStretchLastSection(True)
            health_layout.addWidget(self.preflight_table, 1)
            health_actions = QHBoxLayout()
            self.refresh_health_button = QPushButton("刷新健康状态")
            self.export_diagnostics_button = QPushButton("导出诊断 JSON")
            health_actions.addWidget(self.refresh_health_button)
            health_actions.addWidget(self.export_diagnostics_button)
            health_actions.addStretch(1)
            health_layout.addLayout(health_actions)
            self.refresh_health_button.clicked.connect(self._refresh_health)
            self.export_diagnostics_button.clicked.connect(self._export_diagnostics)
            diagnostics_hint = QLabel("诊断文件会自动脱敏 RTSP 凭据、Webhook 密钥和 SMTP 密码。")
            diagnostics_hint.setWordWrap(True)
            diagnostics_hint.setObjectName("muted")
            health_layout.addWidget(diagnostics_hint)
            layout.addWidget(health, 1)
            return page


else:

    class SettingsPagesMixin:  # pragma: no cover - Qt is optional for the core package
        pass


__all__ = ["SettingsPagesMixin"]
