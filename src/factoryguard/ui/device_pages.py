from __future__ import annotations

"""Focused pages for standard video devices, recordings and local fallback."""

from datetime import datetime, UTC
from typing import Any

from factoryguard.devices.base import DeviceCredentials
from factoryguard.domain.models import (
    AnalysisPolicy,
    CameraDevice,
    DeviceProtocol,
    StreamRole,
    VideoDevice,
    VideoStream,
)
from factoryguard.domain.topology import EntityRegistry
from factoryguard.inference.profiles import ModelProfile

from .qt import HAS_QT


if HAS_QT:
    from .qt import (
        QAbstractItemView,
        QComboBox,
        QDateTimeEdit,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        Qt,
    )


    class DevicePagesMixin:
        """Device-facing settings pages kept separate from notification pages."""

        def _device_topology(self) -> EntityRegistry | None:
            return getattr(self, "_topology", None)

        def _build_device_discovery_page(self) -> QWidget:
            page, layout = self._settings_page(
                "设备发现",
                "扫描现场设备或手工录入地址；设备能力以实际连接结果为准。",
            )
            actions = QHBoxLayout()
            scan_onvif = QPushButton("扫描 ONVIF")
            add_rtsp = QPushButton("添加 RTSP")
            add_gb = QPushButton("GB/T 28181 接入")
            scan_onvif.clicked.connect(self._scan_onvif_devices)
            add_rtsp.clicked.connect(self._add_rtsp_device)
            add_gb.clicked.connect(self._show_gb28181_access)
            actions.addWidget(scan_onvif)
            actions.addWidget(add_rtsp)
            actions.addWidget(add_gb)
            actions.addStretch(1)
            layout.addLayout(actions)

            status = QLabel("尚未扫描")
            status.setObjectName("muted")
            self.device_discovery_status = status
            layout.addWidget(status)
            table = QTableWidget(0, 6)
            table.setHorizontalHeaderLabels(["协议", "地址", "厂商", "型号", "状态", "操作"])
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setStretchLastSection(True)
            self.device_discovery_table = table
            layout.addWidget(table, 1)
            layout.addStretch(1)
            return page

        def _scan_onvif_devices(self) -> None:
            self.device_discovery_status.setText("正在扫描 ONVIF 设备…")
            try:
                from factoryguard.devices.onvif import OnvifDiscovery

                found = OnvifDiscovery().discover(timeout=1.2)
            except Exception as exc:  # noqa: BLE001 - keep network errors on the page
                self.device_discovery_status.setText(f"扫描失败：{exc}")
                return
            self.device_discovery_table.setRowCount(0)
            for item in found:
                row = self.device_discovery_table.rowCount()
                self.device_discovery_table.insertRow(row)
                values = ("ONVIF", item.endpoint, "", "", "发现", "添加")
                for column, value in enumerate(values):
                    self.device_discovery_table.setItem(row, column, QTableWidgetItem(value))
            self.device_discovery_status.setText(f"发现 {len(found)} 台 ONVIF 设备")

        def _add_rtsp_device(self) -> None:
            topology = self._device_topology()
            if topology is None or not topology.sites:
                QMessageBox.information(self, "设备接入", "请先创建厂区和区域")
                return
            site_names = [site.name for site in topology.sites]
            site_name, accepted = QInputDialog.getItem(self, "选择厂区", "厂区", site_names, 0, False)
            if not accepted:
                return
            site = next(item for item in topology.sites if item.name == site_name)
            areas = [area for area in topology.areas if area.site_id == site.id]
            if not areas:
                QMessageBox.information(self, "设备接入", "该厂区还没有区域")
                return
            area_names = [area.name for area in areas]
            area_name, accepted = QInputDialog.getItem(self, "选择区域", "区域", area_names, 0, False)
            if not accepted:
                return
            area = next(item for item in areas if item.name == area_name)
            name, accepted = QInputDialog.getText(self, "设备名称", "设备名称")
            if not accepted or not name.strip():
                return
            endpoint, accepted = QInputDialog.getText(self, "视频地址", "RTSP 地址")
            if not accepted or not endpoint.strip():
                return
            device = VideoDevice(
                id=f"device-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
                site_id=site.id,
                area_id=area.id,
                name=name.strip(),
                protocol=DeviceProtocol.RTSP,
                endpoint=endpoint.strip(),
            )
            camera = CameraDevice(
                id=f"camera-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
                area_id=area.id,
                name=name.strip(),
                device_id=device.id,
                channel_id="ch1",
            )
            stream = VideoStream(
                id=f"stream-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
                camera_id=camera.id,
                name="实时预览",
                url=endpoint.strip(),
                role=StreamRole.MAIN,
                device_id=device.id,
                analysis_policy=AnalysisPolicy.LOCAL_FALLBACK,
            )
            try:
                self._persist_topology_entities((device, camera, stream))
                self.device_discovery_status.setText(f"已添加设备：{name.strip()}")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "设备接入失败", str(exc))

        def _show_gb28181_access(self) -> None:
            manager = getattr(self, "_device_manager", None)
            platform = getattr(manager, "gb_platform", None)
            if platform is None:
                QMessageBox.information(
                    self,
                    "GB/T 28181",
                    "本机 SIP 平台参数将在设备服务启动后显示。",
                )
                return
            rows = manager.gb_registered_devices()
            self.device_discovery_table.setRowCount(0)
            for item in rows:
                row = self.device_discovery_table.rowCount()
                self.device_discovery_table.insertRow(row)
                values = (
                    "GB/T 28181",
                    item.get("device_id", ""),
                    "",
                    "",
                    "已注册",
                    "查看通道",
                )
                for column, value in enumerate(values):
                    self.device_discovery_table.setItem(row, column, QTableWidgetItem(value))
            self.device_discovery_status.setText(f"当前注册 {len(rows)} 台国标设备")

        def _build_device_capabilities_page(self) -> QWidget:
            page, layout = self._settings_page(
                "设备能力",
                "按设备查看连接、通道、录像和事件能力。",
            )
            table = QTableWidget(0, 8)
            table.setHorizontalHeaderLabels(
                ["设备", "协议", "在线", "视频", "原生事件", "录像", "分析策略", "最近探测"]
            )
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setStretchLastSection(True)
            self.device_capability_table = table
            layout.addWidget(table, 1)
            actions = QHBoxLayout()
            refresh = QPushButton("刷新能力")
            probe = QPushButton("测试连接")
            credentials_button = QPushButton("设置凭据")
            refresh.clicked.connect(self._refresh_device_capabilities)
            probe.clicked.connect(self._probe_selected_device)
            credentials_button.clicked.connect(self._set_selected_device_credentials)
            actions.addWidget(refresh)
            actions.addWidget(probe)
            actions.addWidget(credentials_button)
            actions.addStretch(1)
            layout.addLayout(actions)
            self.device_capability_status = QLabel("")
            self.device_capability_status.setObjectName("muted")
            layout.addWidget(self.device_capability_status)
            self._refresh_device_capabilities()
            return page

        def _refresh_device_capabilities(self) -> None:
            table = getattr(self, "device_capability_table", None)
            topology = self._device_topology()
            if table is None:
                return
            table.setRowCount(0)
            for device in topology.devices if topology else ():
                caps = device.capabilities
                values = (
                    device.name,
                    str(device.protocol),
                    "启用" if device.enabled else "停用",
                    "支持" if caps.live_video else "—",
                    "支持" if caps.native_events else "—",
                    "支持" if caps.recording_search else "—",
                    str(device.analysis_policy).replace("_", " "),
                    f"{device.health_status} · {device.last_probe_at or '未探测'}",
                )
                row = table.rowCount()
                table.insertRow(row)
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, device.id)
                    table.setItem(row, column, item)
            self.device_capability_status.setText(f"共 {len(topology.devices) if topology else 0} 台设备")

        def _probe_selected_device(self) -> None:
            topology = self._device_topology()
            table = getattr(self, "device_capability_table", None)
            if topology is None or table is None or not table.selectedItems():
                self.device_capability_status.setText("请先选择设备")
                return
            row = table.currentRow()
            item = table.item(row, 0)
            device_id = item.data(Qt.UserRole) if item else None
            device = next((value for value in topology.devices if value.id == device_id), None)
            if device is None:
                return
            manager = getattr(self, "_device_manager", None)
            if manager is None:
                self.device_capability_status.setText("设备服务尚未启动")
                return
            result = manager.probe(device)
            self.device_capability_status.setText(("连接成功 · " if result.ok else "连接失败 · ") + result.detail)

        def _set_selected_device_credentials(self) -> None:
            topology = self._device_topology()
            table = getattr(self, "device_capability_table", None)
            manager = getattr(self, "_device_manager", None)
            if topology is None or table is None or manager is None or table.currentRow() < 0:
                self.device_capability_status.setText("请先选择设备")
                return
            item = table.item(table.currentRow(), 0)
            device = next((value for value in topology.devices if value.id == (item.data(Qt.UserRole) if item else None)), None)
            if device is None:
                return
            username, accepted = QInputDialog.getText(self, "设备账号", "账号")
            if not accepted:
                return
            password, accepted = QInputDialog.getText(
                self,
                "设备密码",
                "密码",
                QLineEdit.Password,
            )
            if not accepted:
                return
            try:
                manager.save_credentials(
                    device,
                    DeviceCredentials(username=username.strip(), password=password),
                    actor=self._current_actor(),
                )
                self._refresh_entity_tree()
                self._refresh_device_capabilities()
                self.device_capability_status.setText("设备凭据已加密保存")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "凭据保存失败", str(exc))

        def _build_recordings_page(self) -> QWidget:
            page, layout = self._settings_page(
                "录像检索",
                "优先查询 NVR 录像；设备不支持时显示不可用。",
            )
            form = QFormLayout()
            self.recording_device_combo = QComboBox()
            self.recording_channel_edit = QLineEdit()
            self.recording_start_edit = QDateTimeEdit(datetime.now())
            self.recording_end_edit = QDateTimeEdit(datetime.now())
            form.addRow("设备", self.recording_device_combo)
            form.addRow("通道", self.recording_channel_edit)
            form.addRow("开始时间", self.recording_start_edit)
            form.addRow("结束时间", self.recording_end_edit)
            layout.addLayout(form)
            actions = QHBoxLayout()
            search = QPushButton("搜索录像")
            search.clicked.connect(self._search_recordings)
            actions.addWidget(search)
            actions.addStretch(1)
            layout.addLayout(actions)
            table = QTableWidget(0, 5)
            table.setHorizontalHeaderLabels(["设备", "通道", "开始", "结束", "来源"])
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.recording_table = table
            layout.addWidget(table, 1)
            self.recording_status = QLabel("")
            self.recording_status.setObjectName("muted")
            layout.addWidget(self.recording_status)
            self._refresh_recording_devices()
            return page

        def _refresh_recording_devices(self) -> None:
            combo = getattr(self, "recording_device_combo", None)
            topology = self._device_topology()
            if combo is None:
                return
            combo.clear()
            combo.addItem("全部设备", "")
            for device in topology.devices if topology else ():
                combo.addItem(device.name, device.id)

        def _search_recordings(self) -> None:
            self.recording_table.setRowCount(0)
            self.recording_status.setText("设备未提供可检索录像")
            manager = getattr(self, "_device_manager", None)
            if manager is None:
                return
            device_id = self.recording_device_combo.currentData()
            start = self.recording_start_edit.dateTime().toPython().replace(tzinfo=UTC)
            end = self.recording_end_edit.dateTime().toPython().replace(tzinfo=UTC)
            try:
                rows = manager.store.search_recordings(
                    device_id=device_id or None,
                    channel_id=self.recording_channel_edit.text().strip() or None,
                    start_at=start,
                    end_at=end,
                )
            except Exception as exc:  # noqa: BLE001
                self.recording_status.setText(f"检索失败：{exc}")
                return
            for record in rows:
                row = self.recording_table.rowCount()
                self.recording_table.insertRow(row)
                values = (
                    record.get("device_id", ""),
                    record.get("channel_id", ""),
                    record.get("start_at", ""),
                    record.get("end_at", ""),
                    record.get("source", ""),
                )
                for column, value in enumerate(values):
                    self.recording_table.setItem(row, column, QTableWidgetItem(str(value)))
            self.recording_status.setText(f"找到 {len(rows)} 条录像记录")

        def _build_protocol_diagnostics_page(self) -> QWidget:
            page, layout = self._settings_page(
                "协议诊断",
                "查看设备连接、注册、订阅和流状态。",
            )
            table = QTableWidget(0, 5)
            table.setHorizontalHeaderLabels(["对象", "状态", "最近更新", "最近成功", "说明"])
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.horizontalHeader().setStretchLastSection(True)
            self.protocol_diagnostic_table = table
            layout.addWidget(table, 1)
            refresh = QPushButton("刷新诊断")
            refresh.clicked.connect(self._refresh_protocol_diagnostics)
            layout.addWidget(refresh)
            self.protocol_diagnostic_status = QLabel("")
            self.protocol_diagnostic_status.setObjectName("muted")
            layout.addWidget(self.protocol_diagnostic_status)
            self._refresh_protocol_diagnostics()
            return page

        def _refresh_protocol_diagnostics(self) -> None:
            table = getattr(self, "protocol_diagnostic_table", None)
            if table is None:
                return
            table.setRowCount(0)
            manager = getattr(self, "_device_manager", None)
            subscriptions = manager.store.list_device_subscriptions() if manager is not None else {}
            for device_id, value in subscriptions.items():
                row = table.rowCount()
                table.insertRow(row)
                values = (
                    device_id,
                    value.get("state", ""),
                    value.get("updated_at", ""),
                    value.get("last_event_at") or "—",
                    value.get("detail", ""),
                )
                for column, text in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(str(text)))
            if manager is not None and getattr(manager, "gb_platform", None) is not None:
                gb = manager.gb_platform.diagnostics
                row = table.rowCount()
                table.insertRow(row)
                values = (
                    "GB/T 28181 平台",
                    "运行" if gb.get("started") else "待启动",
                    "",
                    "",
                    f"注册 {gb.get('registrations', 0)} · 通道 {gb.get('channels', 0)} · 流 {gb.get('active_streams', 0)}",
                )
                for column, text in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(str(text)))
            stream_health = manager.store.list_stream_health() if manager is not None else {}
            for stream_id, value in stream_health.items():
                row = table.rowCount()
                table.insertRow(row)
                values = (
                    stream_id,
                    value.get("status", ""),
                    value.get("updated_at", ""),
                    value.get("last_ok_at") or "—",
                    value.get("detail", ""),
                )
                for column, text in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(str(text)))
            self.protocol_diagnostic_status.setText(
                f"设备订阅 {len(subscriptions)} 条 · 流状态 {len(stream_health)} 条"
            )

        def _build_local_analysis_page(self) -> QWidget:
            page, layout = self._settings_page(
                "本机备用分析",
                "摄像头缺少所需事件能力时，使用本机 ONNX 完成补充分析。",
            )
            policy_group = QGroupBox("分析策略")
            policy_layout = QFormLayout(policy_group)
            self.local_policy_combo = QComboBox()
            self.local_policy_combo.addItem("设备优先，本机兜底", AnalysisPolicy.LOCAL_FALLBACK.value)
            self.local_policy_combo.addItem("设备原生事件", AnalysisPolicy.NATIVE_ONLY.value)
            self.local_policy_combo.addItem("本机分析优先", AnalysisPolicy.LOCAL_PRIMARY.value)
            self.local_policy_combo.addItem("停用分析", AnalysisPolicy.DISABLED.value)
            current = str(getattr(self, "_local_settings", {}).get("analysis_policy", AnalysisPolicy.LOCAL_FALLBACK.value))
            index = self.local_policy_combo.findData(current)
            self.local_policy_combo.setCurrentIndex(max(0, index))
            policy_layout.addRow("默认策略", self.local_policy_combo)
            save_policy = QPushButton("保存策略")
            save_policy.clicked.connect(self._save_local_analysis_policy)
            policy_layout.addRow("", save_policy)
            layout.addWidget(policy_group)
            model_group = QGroupBox("备用模型")
            model_layout = QVBoxLayout(model_group)
            table = QTableWidget(0, 5)
            table.setHorizontalHeaderLabels(["名称", "模型文件", "标签", "置信度", "状态"])
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.local_model_table = table
            model_layout.addWidget(table)
            model_actions = QHBoxLayout()
            refresh_models = QPushButton("刷新模型")
            manage_models = QPushButton("管理模型")
            refresh_models.clicked.connect(self._refresh_local_models)
            manage_models.clicked.connect(lambda: self._switch_page("settings-models"))
            model_actions.addWidget(refresh_models)
            model_actions.addWidget(manage_models)
            model_actions.addStretch(1)
            model_layout.addLayout(model_actions)
            layout.addWidget(model_group)
            self.local_analysis_status = QLabel("设备原生事件优先，本机分析按需启动")
            self.local_analysis_status.setObjectName("muted")
            layout.addWidget(self.local_analysis_status)
            self._refresh_local_models()
            return page

        def _refresh_local_models(self) -> None:
            table = getattr(self, "local_model_table", None)
            if table is None:
                return
            table.setRowCount(0)
            profiles = getattr(self, "_model_profiles", ())
            for profile in profiles:
                row = table.rowCount()
                table.insertRow(row)
                values = (
                    profile.name,
                    profile.model_uri,
                    ", ".join(profile.labels),
                    f"{profile.confidence:.2f}",
                    "启用" if profile.enabled else "停用",
                )
                for column, value in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(str(value)))

        def _save_local_analysis_policy(self) -> None:
            policy = str(self.local_policy_combo.currentData())
            self._local_settings["analysis_policy"] = policy
            try:
                self._local_settings_path.parent.mkdir(parents=True, exist_ok=True)
                self._local_settings_path.write_text(
                    __import__("json").dumps(self._local_settings, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                self.statusBar().showMessage(f"策略保存失败：{exc}", 6000)
                return
            self.statusBar().showMessage("本机备用分析策略已保存", 5000)


else:

    class DevicePagesMixin:
        pass


__all__ = ["DevicePagesMixin"]
