from __future__ import annotations

"""Dedicated settings pages for the FactoryGuard desktop application."""

import json
from typing import Any

from factoryguard.domain.permissions import ROLE_LABELS, UserRole, role_from_value
from factoryguard.inference.profiles import ModelBinding, ModelProfile, new_model_binding_id, new_model_profile_id
from factoryguard.storage.sqlite import SQLiteStore
from factoryguard.inference.yolo_onnx import YoloOnnxEngine
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
        QHeaderView,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QSpinBox,
        QSplitter,
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
                destination.secrets["secret"] = self._webhook_secret_value()
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
                destination.secrets["password"] = self._email_password_value()
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
                destination.secrets["token"] = self._sms_token_value()

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

        def _build_model_settings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "模型接入",
                "集中管理本机 ONNX 模型，绑定当前监控配置并做健康检查。",
            )
            instances = QGroupBox("模型实例")
            instances_layout = QVBoxLayout(instances)
            self.model_profile_table = QTableWidget(0, 6)
            self.model_profile_table.setHorizontalHeaderLabels(
                ["名称", "任务", "后端", "模型地址", "版本", "状态"]
            )
            self.model_profile_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.model_profile_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.model_profile_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.model_profile_table.setAlternatingRowColors(True)
            self.model_profile_table.horizontalHeader().setStretchLastSection(True)
            self.model_profile_table.itemSelectionChanged.connect(self._select_model_profile_from_table)
            instances_layout.addWidget(self.model_profile_table)
            actions = QHBoxLayout()
            add_model = QPushButton("新增模型")
            duplicate_model = QPushButton("复制模型")
            delete_model = QPushButton("删除模型")
            add_model.clicked.connect(self._add_model_profile)
            duplicate_model.clicked.connect(self._duplicate_model_profile)
            delete_model.clicked.connect(self._delete_model_profile)
            actions.addWidget(add_model)
            actions.addWidget(duplicate_model)
            actions.addWidget(delete_model)
            actions.addStretch(1)
            instances_layout.addLayout(actions)
            layout.addWidget(instances)

            editor = QGroupBox("模型配置")
            editor_layout = QFormLayout(editor)
            self.model_name_edit = QLineEdit()
            self.model_name_edit.setPlaceholderText("例如：YOLO11n 人员检测")
            self.model_task_combo = QComboBox()
            self.model_task_combo.addItem("目标检测", "detection")
            self.model_task_combo.addItem("图像分类", "classification")
            self.model_task_combo.addItem("姿态估计", "pose")
            self.model_task_combo.addItem("异常检测", "anomaly")
            self.model_backend_combo = QComboBox()
            self.model_backend_combo.addItem("本地 ONNX Runtime", "onnx_cpu")
            self.model_backend_combo.setEnabled(False)
            self.model_uri_edit = QLineEdit()
            self.model_uri_edit.setPlaceholderText("models/yolo11n.onnx")
            self.model_input_width_spin = QSpinBox()
            self.model_input_width_spin.setRange(64, 4096)
            self.model_input_width_spin.setValue(640)
            self.model_input_height_spin = QSpinBox()
            self.model_input_height_spin.setRange(64, 4096)
            self.model_input_height_spin.setValue(640)
            self.model_labels_edit = QLineEdit("person")
            self.model_labels_edit.setPlaceholderText("person, forklift, vehicle")
            self.model_confidence_spin = QDoubleSpinBox()
            self.model_confidence_spin.setRange(0.01, 1.0)
            self.model_confidence_spin.setSingleStep(0.05)
            self.model_confidence_spin.setValue(0.25)
            self.model_iou_spin = QDoubleSpinBox()
            self.model_iou_spin.setRange(0.01, 1.0)
            self.model_iou_spin.setSingleStep(0.05)
            self.model_iou_spin.setValue(0.45)
            self.model_version_edit = QLineEdit("1.0")
            self.model_enabled_check = QCheckBox("模型启用")
            self.model_enabled_check.setChecked(True)
            self.model_status_label = QLabel("等待测试")
            self.model_status_label.setWordWrap(True)
            self.model_status_label.setObjectName("muted")
            editor_layout.addRow("模型名称", self.model_name_edit)
            editor_layout.addRow("任务类型", self.model_task_combo)
            editor_layout.addRow("运行后端", self.model_backend_combo)
            editor_layout.addRow("模型地址", self.model_uri_edit)
            size_row = QHBoxLayout()
            size_row.addWidget(QLabel("宽"))
            size_row.addWidget(self.model_input_width_spin)
            size_row.addWidget(QLabel("高"))
            size_row.addWidget(self.model_input_height_spin)
            editor_layout.addRow("输入尺寸", size_row)
            editor_layout.addRow("标签集合", self.model_labels_edit)
            editor_layout.addRow("默认置信度", self.model_confidence_spin)
            editor_layout.addRow("默认 IoU", self.model_iou_spin)
            editor_layout.addRow("模型版本", self.model_version_edit)
            editor_layout.addRow("状态", self.model_enabled_check)
            editor_layout.addRow("检查结果", self.model_status_label)
            button_row = QHBoxLayout()
            test_model = QPushButton("健康检查")
            apply_model = QPushButton("应用到监控配置")
            save_models = QPushButton("保存模型接入")
            save_models.setObjectName("primary")
            self.model_profile_test_button = test_model
            test_model.clicked.connect(self._test_model_profile)
            apply_model.clicked.connect(self._apply_model_profile)
            save_models.clicked.connect(self._save_model_profiles)
            button_row.addWidget(test_model)
            button_row.addWidget(apply_model)
            button_row.addWidget(save_models)
            button_row.addStretch(1)
            editor_layout.addRow("", button_row)
            layout.addWidget(editor)

            binding_group = QGroupBox("模型绑定")
            binding_layout = QVBoxLayout(binding_group)
            self.model_binding_table = QTableWidget(0, 5)
            self.model_binding_table.setHorizontalHeaderLabels(["模型", "范围", "范围 ID", "优先级", "状态"])
            self.model_binding_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.model_binding_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.model_binding_table.setAlternatingRowColors(True)
            self.model_binding_table.horizontalHeader().setStretchLastSection(True)
            binding_layout.addWidget(self.model_binding_table)
            binding_form = QHBoxLayout()
            self.binding_model_combo = QComboBox()
            self.binding_scope_combo = QComboBox()
            self.binding_scope_combo.addItem("厂区", "site")
            self.binding_scope_combo.addItem("区域", "area")
            self.binding_scope_combo.addItem("摄像头", "camera")
            self.binding_scope_combo.addItem("推流", "stream")
            self.binding_scope_id_edit = QLineEdit()
            self.binding_scope_id_edit.setPlaceholderText("选择或输入实体 ID")
            self.binding_priority_spin = QSpinBox()
            self.binding_priority_spin.setRange(-100, 100)
            self.binding_enabled_check = QCheckBox("启用")
            self.binding_enabled_check.setChecked(True)
            add_binding = QPushButton("新增绑定")
            delete_binding = QPushButton("删除绑定")
            add_binding.clicked.connect(self._add_model_binding)
            delete_binding.clicked.connect(self._delete_model_binding)
            binding_form.addWidget(self.binding_model_combo, 2)
            binding_form.addWidget(self.binding_scope_combo)
            binding_form.addWidget(self.binding_scope_id_edit, 2)
            binding_form.addWidget(QLabel("优先级"))
            binding_form.addWidget(self.binding_priority_spin)
            binding_form.addWidget(self.binding_enabled_check)
            binding_form.addWidget(add_binding)
            binding_form.addWidget(delete_binding)
            binding_layout.addLayout(binding_form)
            layout.addWidget(binding_group)
            layout.addStretch(1)
            self._refresh_model_profile_table()
            self._refresh_model_binding_table()
            selected = self._model_profile_by_id(self._selected_model_profile_id)
            if selected is not None:
                self._load_model_profile_editor(selected)
            return page

        def _refresh_model_binding_model_combo(self) -> None:
            if not hasattr(self, "binding_model_combo"):
                return
            current = self.binding_model_combo.currentData()
            self.binding_model_combo.clear()
            for profile in self._model_profiles:
                self.binding_model_combo.addItem(profile.name, profile.id)
            index = self.binding_model_combo.findData(current)
            self.binding_model_combo.setCurrentIndex(max(0, index))

        def _refresh_model_binding_table(self) -> None:
            if not hasattr(self, "model_binding_table"):
                return
            model_names = {profile.id: profile.name for profile in self._model_profiles}
            scope_labels = {"site": "厂区", "area": "区域", "camera": "摄像头", "stream": "推流"}
            self.model_binding_table.setRowCount(0)
            for binding in self._model_bindings:
                row = self.model_binding_table.rowCount()
                self.model_binding_table.insertRow(row)
                values = (
                    model_names.get(binding.model_profile_id, binding.model_profile_id),
                    scope_labels.get(binding.scope_type, binding.scope_type),
                    binding.scope_id,
                    str(binding.priority),
                    "启用" if binding.enabled else "停用",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.UserRole, binding.id)
                    self.model_binding_table.setItem(row, column, item)
            self._refresh_model_binding_model_combo()

        def _add_model_binding(self) -> None:
            profile_id = str(self.binding_model_combo.currentData() or "")
            scope_id = self.binding_scope_id_edit.text().strip()
            if not profile_id or not scope_id:
                self.statusBar().showMessage("请选择模型并填写绑定范围 ID", 5000)
                return
            try:
                binding = ModelBinding(
                    id=new_model_binding_id(),
                    model_profile_id=profile_id,
                    scope_type=str(self.binding_scope_combo.currentData() or "stream"),
                    scope_id=scope_id,
                    priority=self.binding_priority_spin.value(),
                    enabled=self.binding_enabled_check.isChecked(),
                )
            except (TypeError, ValueError) as exc:
                self.statusBar().showMessage(f"模型绑定无效：{exc}", 6000)
                return
            self._model_bindings.append(binding)
            self.binding_scope_id_edit.clear()
            self._refresh_model_binding_table()

        def _delete_model_binding(self) -> None:
            selected = self.model_binding_table.selectedItems()
            if not selected:
                return
            binding_id = str(selected[0].data(Qt.UserRole) or "")
            self._model_bindings = [binding for binding in self._model_bindings if binding.id != binding_id]
            self._refresh_model_binding_table()

        def _refresh_model_profile_table(self) -> None:
            self._model_table_refreshing = True
            self.model_profile_table.setRowCount(0)
            selected_id = self._selected_model_profile_id
            task_labels = {"detection": "目标检测", "classification": "分类", "pose": "姿态", "anomaly": "异常"}
            backend_labels = {"onnx_cpu": "本地 ONNX"}
            for profile in self._model_profiles:
                row = self.model_profile_table.rowCount()
                self.model_profile_table.insertRow(row)
                values = (
                    profile.name,
                    task_labels.get(profile.task_type, profile.task_type),
                    backend_labels.get(profile.backend, profile.backend),
                    profile.model_uri,
                    profile.version,
                    "启用" if profile.enabled else "停用",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setData(Qt.UserRole, profile.id)
                    self.model_profile_table.setItem(row, column, item)
                if profile.id == selected_id:
                    self.model_profile_table.selectRow(row)
            self._model_table_refreshing = False
            self._refresh_model_binding_model_combo()

        def _select_model_profile_from_table(self) -> None:
            if getattr(self, "_model_table_refreshing", False):
                return
            selected = self.model_profile_table.selectedItems()
            if not selected:
                return
            profile_id = str(selected[0].data(Qt.UserRole) or "")
            if not profile_id or profile_id == self._selected_model_profile_id:
                return
            self._sync_model_editor_to_profile()
            self._selected_model_profile_id = profile_id
            profile = self._model_profile_by_id(profile_id)
            if profile is not None:
                self._load_model_profile_editor(profile)

        def _sync_model_editor_to_profile(self) -> None:
            profile = self._model_profile_by_id(self._selected_model_profile_id)
            if profile is None:
                return
            profile.name = self.model_name_edit.text().strip() or profile.name
            profile.task_type = str(self.model_task_combo.currentData() or "detection")
            profile.backend = str(self.model_backend_combo.currentData() or "onnx_cpu")
            profile.model_uri = self.model_uri_edit.text().strip()
            profile.input_width = self.model_input_width_spin.value()
            profile.input_height = self.model_input_height_spin.value()
            profile.labels = tuple(
                item.strip() for item in self.model_labels_edit.text().split(",") if item.strip()
            )
            profile.confidence = self.model_confidence_spin.value()
            profile.iou = self.model_iou_spin.value()
            profile.version = self.model_version_edit.text().strip() or profile.version
            profile.enabled = self.model_enabled_check.isChecked()

        def _load_model_profile_editor(self, profile: ModelProfile) -> None:
            self.model_name_edit.setText(profile.name)
            self.model_task_combo.setCurrentIndex(max(0, self.model_task_combo.findData(profile.task_type)))
            self.model_backend_combo.setCurrentIndex(max(0, self.model_backend_combo.findData(profile.backend)))
            self.model_uri_edit.setText(profile.model_uri)
            self.model_input_width_spin.setValue(profile.input_width)
            self.model_input_height_spin.setValue(profile.input_height)
            self.model_labels_edit.setText(", ".join(profile.labels))
            self.model_confidence_spin.setValue(profile.confidence)
            self.model_iou_spin.setValue(profile.iou)
            self.model_version_edit.setText(profile.version)
            self.model_enabled_check.setChecked(profile.enabled)
            self.model_status_label.setText("等待测试")

        def _add_model_profile(self) -> None:
            self._sync_model_editor_to_profile()
            profile = ModelProfile(
                id=new_model_profile_id(),
                name="新模型",
                task_type="detection",
                backend="onnx_cpu",
                model_uri="",
                labels=("person",),
            )
            self._model_profiles.append(profile)
            self._selected_model_profile_id = profile.id
            self._refresh_model_profile_table()
            self._load_model_profile_editor(profile)

        def _duplicate_model_profile(self) -> None:
            self._sync_model_editor_to_profile()
            source = self._model_profile_by_id(self._selected_model_profile_id)
            if source is None:
                return
            profile = ModelProfile.from_public_dict({**source.to_public_dict(), "id": new_model_profile_id(), "name": f"{source.name} 副本"})
            self._model_profiles.append(profile)
            self._selected_model_profile_id = profile.id
            self._refresh_model_profile_table()
            self._load_model_profile_editor(profile)

        def _delete_model_profile(self) -> None:
            profile = self._model_profile_by_id(self._selected_model_profile_id)
            if profile is None:
                return
            if len(self._model_profiles) <= 1:
                self.statusBar().showMessage("至少保留一个模型配置", 5000)
                return
            self._model_profiles.remove(profile)
            self._model_bindings = [
                binding for binding in self._model_bindings if binding.model_profile_id != profile.id
            ]
            self._selected_model_profile_id = self._model_profiles[0].id
            self._refresh_model_profile_table()
            self._load_model_profile_editor(self._model_profiles[0])

        def _save_model_profiles(self) -> None:
            self._sync_model_editor_to_profile()
            try:
                self._model_profiles = [
                    ModelProfile.from_public_dict(profile.to_public_dict())
                    for profile in self._model_profiles
                ]
            except (TypeError, ValueError) as exc:
                self.statusBar().showMessage(f"模型配置无效：{exc}", 6000)
                return
            self._local_settings["models"] = {
                "profiles": [profile.to_public_dict() for profile in self._model_profiles],
                "bindings": [binding.to_public_dict() for binding in self._model_bindings],
            }
            try:
                db_path = self.db_edit.text().strip() or self._base_config.db
                if db_path != ":memory:":
                    store = SQLiteStore(db_path)
                    try:
                        store.save_model_configuration(self._model_profiles, self._model_bindings, actor=self._current_actor())
                    finally:
                        store.close()
            except (OSError, ValueError, RuntimeError) as exc:
                self.statusBar().showMessage(f"模型配置数据库保存失败：{exc}", 6000)
            try:
                self._local_settings_path.parent.mkdir(parents=True, exist_ok=True)
                self._local_settings_path.write_text(
                    json.dumps(self._local_settings, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.statusBar().showMessage(f"模型配置保存失败：{exc}", 6000)
                return
            self._refresh_model_profile_table()
            self._refresh_model_binding_table()
            self.statusBar().showMessage("模型接入配置已保存", 5000)

        def _test_model_profile(self) -> None:
            self._sync_model_editor_to_profile()
            profile = self._model_profile_by_id(self._selected_model_profile_id)
            if profile is None:
                return
            self._start_model_health_check(
                model_path=profile.model_uri,
                status_label=self.model_status_label,
                button=self.model_profile_test_button,
                confidence=profile.confidence,
                iou=profile.iou,
            )

        def _apply_model_profile(self) -> None:
            self._sync_model_editor_to_profile()
            profile = self._model_profile_by_id(self._selected_model_profile_id)
            if profile is None:
                return
            self.model_edit.setText(profile.model_uri)
            self.confidence_spin.setValue(profile.confidence)
            self.statusBar().showMessage(f"已将模型“{profile.name}”应用到本机监控配置", 5000)
        def _build_inference_settings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "本机推理设置",
                "当前产品只使用本机 ONNX Runtime，不需要独立推理服务或中心服务器。",
            )
            service = QGroupBox("本机推理")
            service_layout = QFormLayout(service)
            self.inference_mode_label = QLabel("本机 ONNX Runtime · CPUExecutionProvider")
            self.inference_mode_label.setWordWrap(True)
            model_label = QLabel(self.model_edit.text().strip() or self._base_config.model)
            model_label.setWordWrap(True)
            device_label = QLabel("仅使用 CPUExecutionProvider，不加载 GPU 或远程推理后端")
            device_label.setWordWrap(True)
            self.inference_status_label = QLabel("模型将在启动监控时加载到本机 ONNX Runtime")
            self.inference_status_label.setWordWrap(True)
            self.inference_status_label.setObjectName("muted")
            service_layout.addRow("推理方式", self.inference_mode_label)
            service_layout.addRow("模型文件", model_label)
            service_layout.addRow("执行方式", device_label)
            service_layout.addRow("当前状态", self.inference_status_label)
            action_row = QHBoxLayout()
            test_model = QPushButton("检查本机模型")
            self.model_test_button = test_model
            test_model.clicked.connect(self._test_local_inference_environment)
            action_row.addWidget(test_model)
            action_row.addStretch(1)
            service_layout.addRow("", action_row)
            layout.addWidget(service)

            guidance = QGroupBox("部署说明")
            guidance_layout = QVBoxLayout(guidance)
            costs = QLabel(
                "模型、视频帧、事件和通知都在这台部署电脑上处理。"
                "普通电脑可先使用 CPU；路数或帧率增加时，再为同一台电脑配置可用的 GPU 加速。"
            )
            costs.setWordWrap(True)
            costs.setObjectName("muted")
            guidance_layout.addWidget(costs)
            layout.addWidget(guidance)
            layout.addStretch(1)
            return page

        def _test_local_inference_environment(self) -> None:
            self._start_model_health_check(
                model_path=self.model_edit.text().strip() or self._base_config.model,
                status_label=self.inference_status_label,
                button=self.model_test_button,
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
            self.webhook_test_button = QPushButton("发送测试 Webhook")
            save_webhook = QPushButton("保存 Webhook 配置")
            save_webhook.setObjectName("primary")
            self.webhook_test_button.clicked.connect(lambda: self._send_notification_test("webhook"))
            save_webhook.clicked.connect(self._save_notification_settings)
            webhook_actions = QHBoxLayout()
            webhook_actions.addWidget(self.webhook_test_button)
            webhook_actions.addWidget(save_webhook)
            webhook_actions.addStretch(1)
            connection_layout.addRow("", webhook_actions)
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
            self.email_test_button = QPushButton("发送测试邮件")
            save_email = QPushButton("保存邮件配置")
            save_email.setObjectName("primary")
            validate_email.clicked.connect(self._validate_email_configuration)
            self.email_test_button.clicked.connect(lambda: self._send_notification_test("email"))
            save_email.clicked.connect(self._save_notification_settings)
            action_row.addWidget(validate_email)
            action_row.addWidget(self.email_test_button)
            action_row.addWidget(save_email)
            action_row.addStretch(1)
            connection_layout.addRow("", action_row)
            hint = QLabel("校验只检查地址和参数格式；“发送测试邮件”会连接 SMTP 并发送一条合成测试消息。")
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
                    password=self._email_password_value(),
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
            self.sms_test_button = QPushButton("发送测试短信")
            save_sms = QPushButton("保存短信配置")
            save_sms.setObjectName("primary")
            validate_sms.clicked.connect(self._validate_sms_configuration)
            self.sms_test_button.clicked.connect(lambda: self._send_notification_test("sms"))
            save_sms.clicked.connect(self._save_notification_settings)
            action_row.addWidget(validate_sms)
            action_row.addWidget(self.sms_test_button)
            action_row.addWidget(save_sms)
            action_row.addStretch(1)
            connection_layout.addRow("", action_row)
            hint = QLabel(
                "通用网关请求体包含 provider、to、sender、template_id、message 和 event；"
                "“发送测试短信”会调用网关并发送一条合成测试消息。"
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
                    token=self._sms_token_value(),
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
                "资源监控",
                "查看本机 CPU、内存、磁盘和 FactoryGuard 进程资源占用，并保留关键组件检查。",
            )
            resource = QGroupBox("\u8ba1\u7b97\u673a\u4e0e FactoryGuard")
            resource_layout = QVBoxLayout(resource)
            self.health_label = QLabel("\u7b49\u5f85\u8d44\u6e90\u5237\u65b0")
            self.health_label.setWordWrap(True)
            self.health_label.setObjectName("muted")
            resource_layout.addWidget(self.health_label)
            self.resource_table = QTableWidget(0, 2)
            self.resource_table.setHorizontalHeaderLabels(["\u8d44\u6e90\u6307\u6807", "\u5f53\u524d\u503c"])
            self.resource_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.resource_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.resource_table.setAlternatingRowColors(True)
            self.resource_table.verticalHeader().setVisible(False)
            self.resource_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            self.resource_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            resource_layout.addWidget(self.resource_table, 1)

            checks = QGroupBox("\u5173\u952e\u7ec4\u4ef6")
            checks_layout = QVBoxLayout(checks)
            self.preflight_table = QTableWidget(0, 3)
            self.preflight_table.setHorizontalHeaderLabels(["\u68c0\u67e5\u9879", "\u72b6\u6001", "\u8be6\u60c5"])
            self.preflight_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.preflight_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.preflight_table.setAlternatingRowColors(True)
            self.preflight_table.verticalHeader().setVisible(False)
            self.preflight_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            self.preflight_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            self.preflight_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
            checks_layout.addWidget(self.preflight_table, 1)
            health_actions = QHBoxLayout()
            self.refresh_health_button = QPushButton("\u5237\u65b0\u8d44\u6e90")
            self.export_diagnostics_button = QPushButton("\u5bfc\u51fa\u8bca\u65ad JSON")
            health_actions.addWidget(self.refresh_health_button)
            health_actions.addWidget(self.export_diagnostics_button)
            health_actions.addStretch(1)
            checks_layout.addLayout(health_actions)
            diagnostics_hint = QLabel("\u8bca\u65ad\u6587\u4ef6\u4f1a\u81ea\u52a8\u8131\u654f RTSP \u51ed\u636e\u3001Webhook \u5bc6\u94a5\u548c SMTP \u5bc6\u7801\u3002")
            diagnostics_hint.setWordWrap(True)
            diagnostics_hint.setObjectName("muted")
            checks_layout.addWidget(diagnostics_hint)

            columns = QSplitter(Qt.Horizontal)
            columns.setChildrenCollapsible(False)
            columns.addWidget(resource)
            columns.addWidget(checks)
            columns.setStretchFactor(0, 3)
            columns.setStretchFactor(1, 2)
            columns.setSizes([720, 480])
            layout.addWidget(columns, 1)
            self.refresh_health_button.clicked.connect(self._refresh_health)
            self.export_diagnostics_button.clicked.connect(self._export_diagnostics)
            return page

else:

    class SettingsPagesMixin:  # pragma: no cover - Qt is optional for the core package
        pass


__all__ = ["SettingsPagesMixin"]
