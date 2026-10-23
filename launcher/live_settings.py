"""Apply independent settings immediately and debounce editable text/disk writes."""
from PySide6.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit, QSpinBox
from .slider_ui import GlassSlider


APPEARANCE_FIELDS = {"color_scheme","background_path","background_mode","background_preset","background_opacity","glass_opacity","glass_refraction"}


class LiveSettings:
    def connect_live_settings(self):
        controls = dict(self.settings_fields)
        controls.update(memory=self.memory_slider,memory_auto=self.memory_auto_check,java_auto=self.java_auto,
                        animations=self.animations_check,download_parallel=self.download_parallel,
                        ai_crash_analysis=self.ai_crash_check,ai_auto_preflight=self.ai_preflight_check,
                        ai_clear_key=self.ai_clear_key,background_opacity=self.background_opacity,
                        glass_opacity=self.glass_opacity,glass_refraction=self.glass_refraction)
        for key,widget in controls.items():
            if isinstance(widget,QComboBox):
                widget.currentIndexChanged.connect(lambda _=None,k=key,w=widget:self.queue_setting(k,w.currentData()))
            elif isinstance(widget,QSpinBox):
                widget.setKeyboardTracking(False)
                widget.valueChanged.connect(lambda value,k=key:self.queue_setting(k,value))
            elif isinstance(widget,GlassSlider):
                widget.dragStarted.connect(lambda k=key:self.dragging_settings.add(k))
                widget.valueChanged.connect(lambda value,k=key:self.queue_setting(k,value,-1 if k in self.dragging_settings else 0))
                widget.dragFinished.connect(lambda _=None,k=key:self.release_setting_slider(k))
                widget.dragCanceled.connect(lambda k=key,w=widget:self.cancel_setting_slider(k,w))
            elif isinstance(widget,QPlainTextEdit):
                widget.textChanged.connect(lambda k=key,w=widget:self.queue_setting(k,w.toPlainText(),350))
            elif isinstance(widget,QLineEdit):
                widget.textChanged.connect(lambda value,k=key:self.queue_setting(k,value.strip(),350))
                widget.editingFinished.connect(lambda k=key:self.commit_setting(k))
            else:
                widget.toggled.connect(lambda value,k=key:self.queue_setting(k,value))

    def queue_setting(self,key,value,delay=0):
        self.setting_changes[key] = value
        self.settings_draft[key] = value
        if (key in APPEARANCE_FIELDS or key == "animations") and key not in self.dragging_settings:
            self.apply_setting_effects(key)
        if delay>0:
            self.settings_timer.start(delay)
        elif delay == 0:
            self.commit_setting(key)

    def release_setting_slider(self,key):
        self.dragging_settings.discard(key)
        self.commit_setting(key)

    def cancel_setting_slider(self,key,widget):
        self.dragging_settings.discard(key)
        self.setting_changes.pop(key,None)
        value = self.service.store.config()[key]
        self.settings_draft[key] = value
        widget.blockSignals(True)
        widget.setValue(value)
        widget.blockSignals(False)
        if key == "memory": self.update_memory_preview()

    def apply_setting_effects(self,key):
        config = dict(self.service.store.public_config())
        config.update({k:v for k,v in self.setting_changes.items() if k not in self.dragging_settings and (k in APPEARANCE_FIELDS or k == "animations")})
        self.data["config"] = config
        if key == "animations":
            self.animations = config["animations"]
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            app.setProperty("animations",self.animations)
            app.wheel_router.enabled = self.animations
            if not self.animations:
                self.navigation_switch.select(self.navigation_switch.current,False)
        if key in APPEARANCE_FIELDS:
            self.apply_appearance()
        if key in ("launcher_width","launcher_height"):
            self.apply_window_size()

    def commit_setting(self,key):
        if key in self.dragging_settings:
            return True
        if key not in self.setting_changes:
            return True
        value = self.setting_changes[key]
        try:
            # An empty password field preserves the encrypted credential.
            if key not in ("ai_api_key","curseforge_key") or value:
                result = self.service.config_update({key:value})
                self.data["config"].update(result)
            self.setting_changes.pop(key,None)
            self.setting_errors.pop(key,None)
            if key in ("ai_api_key","curseforge_key"):
                self.settings_draft.pop(key,None)
            if key == "ai_clear_key" and value:
                self.setting_changes.pop("ai_api_key",None)
                self.settings_draft.pop("ai_api_key",None)
                self.settings_draft["ai_clear_key"] = False
                self.ai_clear_key.blockSignals(True)
                self.ai_clear_key.setChecked(False)
                self.ai_clear_key.blockSignals(False)
                field = self.settings_fields["ai_api_key"]
                field.blockSignals(True)
                field.clear()
                field.blockSignals(False)
                field.setPlaceholderText("API Key")
            elif key in ("ai_api_key","curseforge_key") and value:
                self.settings_fields[key].setPlaceholderText("已保存；留空保留")
            if key in APPEARANCE_FIELDS or key in ("animations","launcher_width","launcher_height"):
                self.apply_setting_effects(key)
            return True
        except Exception as exc:
            message = str(exc)
            if self.setting_errors.get(key) != message:
                self.toast("此项设置未应用："+message,True)
            self.setting_errors[key] = message
            return False

    def flush_settings(self):
        self.settings_timer.stop()
        results = [self.commit_setting(key) for key in list(self.setting_changes)]
        return all(results)

    def queue_instance_settings(self,instance_id,values,delay=0):
        self.instance_drafts[instance_id] = values
        if delay:
            self.instance_settings_timer.start(delay)
        else:
            self.flush_instance_settings()

    def flush_instance_settings(self):
        self.instance_settings_timer.stop()
        results = [self.save_instance_settings(key,values) for key,values in list(self.instance_drafts.items())]
        return all(results)

    def save_instance_settings(self,instance_id,values):
        error_key = "instance:"+instance_id
        try:
            self.service.route("POST","/api/instances/update",{},dict(values,id=instance_id))
            self.instance_drafts.pop(instance_id,None)
            self.setting_errors.pop(error_key,None)
            self.data["instances"] = self.service.store.instances()
            return True
        except Exception as exc:
            message = str(exc)
            if self.setting_errors.get(error_key) != message:
                self.toast("版本设置未应用："+message,True)
            self.setting_errors[error_key] = message
            return False
