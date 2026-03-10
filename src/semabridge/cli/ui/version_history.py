"""
Version History Widget.

Timeline view of configuration changes with commit-style
history and rollback capabilities.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QGroupBox,
    QTextEdit,
    QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class VersionHistoryWidget(QWidget):
    """
    Version history panel for configuration tracking.
    
    Features:
    - Timeline of changes (commit-style)
    - Change author and timestamp
    - Commit message/description
    - Rollback and compare actions
    - Powered by VersionManager (YAML) and DuckDBManager (Models)
    """
    
    version_selected = pyqtSignal(dict)  # Emitted when a version is selected
    compare_requested = pyqtSignal(str, str)  # (left_version_id, right_version_id)
    
    def __init__(self, version_manager, config_manager, duckdb_manager=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.version_manager = version_manager
        self.config_manager = config_manager
        self.duckdb_manager = duckdb_manager
        self._versions: List[Dict[str, Any]] = []
        self._model_id: Optional[str] = None  # Current model ID filter
        
        self._setup_ui()
        self.refresh()
    
    def _setup_ui(self):
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)
        
        # Header
        header = QHBoxLayout()
        self._title = QLabel("Version History")
        self._title.setProperty("class", "title")
        layout.addWidget(self._title)
        
        header.addStretch()
        
        layout.addLayout(header)
        
        
        # Version list
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSpacing(2)
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection) # Allow multiple selection
        self._list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)
        
        # Selected version details
        details_group = QGroupBox("Details")
        details_layout = QVBoxLayout(details_group)
        details_layout.setSpacing(4)
        
        # Version info
        self._version_label = QLabel("Select a version")
        self._version_label.setProperty("class", "monospace")
        details_layout.addWidget(self._version_label)
        
        self._timestamp_label = QLabel("")
        self._timestamp_label.setProperty("class", "subtitle")
        details_layout.addWidget(self._timestamp_label)
        
        # Change description
        self._description = QTextEdit()
        self._description.setReadOnly(True)
        self._description.setMaximumHeight(80)
        self._description.setPlaceholderText("Change description")
        details_layout.addWidget(self._description)
        
        layout.addWidget(details_group)
        
        # Action buttons
        actions = QHBoxLayout()
        
        compare_btn = QPushButton("⇔ Compare")
        compare_btn.setProperty("class", "secondary")
        compare_btn.setToolTip("Compare with current config")
        compare_btn.clicked.connect(self._on_compare)
        actions.addWidget(compare_btn)
        
        rollback_btn = QPushButton("↩ Rollback")
        rollback_btn.setProperty("class", "secondary")
        rollback_btn.setToolTip("Restore this version")
        rollback_btn.clicked.connect(self._on_rollback)
        actions.addWidget(rollback_btn)
        
        layout.addLayout(actions)
    
    def refresh(self):
        """Refresh version history from backend."""
        try:
            if self._model_id and self.duckdb_manager:
                # Load from DuckDB
                snapshots = self.duckdb_manager.list_snapshots(self._model_id)
                self._versions = [
                    {
                        "version_id": s.snapshot_id,
                        "timestamp": datetime.fromisoformat(s.timestamp.replace('Z', '+00:00')).timestamp(),
                        "description": s.version_tag or f"Snapshot {s.snapshot_id[:8]}",
                        "status": s.status,
                        "sml_blob": s.sml_blob,
                        "type": "model"
                    }
                    for s in snapshots
                ]
            else:
                # Load from VersionManager (YAML)
                self._versions = self.version_manager.list_versions()
                for v in self._versions:
                    v["type"] = "config"
            
            self._populate_list()
        except Exception as e:
            logger.error(f"Failed to refresh history: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def set_model_context(self, model_data: Optional[Dict[str, Any]] = None):
        """Focus history on a specific model."""
        if isinstance(model_data, dict):
            # For Fabric, ID is the GUID
            self._model_id = model_data.get("id")
            model_name = model_data.get("displayName", self._model_id)
            self._title.setText(f"History: {model_name}")
            logger.info(f"Setting model context to {self._model_id} ({model_name})")
        elif isinstance(model_data, str):
            self._model_id = model_data
            self._title.setText(f"History: {model_data}")
        else:
            self._model_id = None
            self._title.setText("Global Config History")
            logger.info("Clearing model context")
            
        self.refresh()
            
    def _populate_list(self):
        """Populate the version list."""
        self._list.clear()
        
        for version in self._versions:
            # Format: "v123456... - Description"
            vid = version.get("version_id", "unknown")
            desc = version.get("description", "No description")
            ts = version.get("timestamp", 0)
            
            # Simple readable label
            dt = datetime.fromtimestamp(ts).strftime("%H:%M")
            text = f"{dt} - {desc}"
            
            item = QListWidgetItem(text)
            
            # Highlight status
            if version.get("status") == "failed":
                item.setForeground(QColor("#EF4444")) # Error color
            
            item.setData(Qt.ItemDataRole.UserRole, version)
            self._list.addItem(item)
    
    def _on_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        """Handle version selection change."""
        if current is None:
            return
        
        version = current.data(Qt.ItemDataRole.UserRole)
        if version:
            vid = version.get("version_id", "???")
            ts = version.get("timestamp", 0)
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            
            self._version_label.setText(f'{vid[:15]}...')
            self._timestamp_label.setText(f'📅 {dt}')
            self._description.setText(version.get("description", ""))
            
            self.version_selected.emit(version)
            
    def _on_compare(self):
        """Handle compare button click."""
        selected_items = self._list.selectedItems()
        if not selected_items:
            # Fallback to current item if no selection (shouldn't happen with button enabled)
            item = self._list.currentItem()
            if not item: return
            selected_items = [item]
            
        if len(selected_items) == 2:
            # Compare two specific versions
            v1 = selected_items[0].data(Qt.ItemDataRole.UserRole)
            v2 = selected_items[1].data(Qt.ItemDataRole.UserRole)
            # Ensure chronological order (optional, main_window handles left/right)
            self.compare_requested.emit(v1['version_id'], v2['version_id'])
        else:
            # Compare one version against current/HEAD
            version = selected_items[0].data(Qt.ItemDataRole.UserRole)
            version_id = version['version_id']
            self.compare_requested.emit(version_id, "current")

    def _on_rollback(self):
        """Handle rollback button click."""
        from PyQt6.QtWidgets import QMessageBox
        
        item = self._list.currentItem()
        if not item:
            return
            
        version = item.data(Qt.ItemDataRole.UserRole)
        version_id = version['version_id']
        
        # Confirm rollback
        result = QMessageBox.question(
            self,
            "Confirm Rollback",
            f"Rollback to {version.get('description')}?\n\n"
            f"This will restore the semantic state from this version.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if result != QMessageBox.StandardButton.Yes:
            return

        if version.get("type") == "model" and self.duckdb_manager:
            logger.info(f"Rolling back model to {version_id}")
            # Execute DuckDB rollback (creates a new snapshot)
            try:
                success, new_id, _ = self.duckdb_manager.rollback(
                    self._model_id, 
                    version_id, 
                    f"Rollback to {version_id[:8]}"
                )
                if success:
                    QMessageBox.information(self, "Rollback Complete", f"Restored model to version {version_id[:8]}")
                else:
                    QMessageBox.warning(self, "Rollback Aborted", "Model is already at this version.")
            except Exception as e:
                logger.error(f"DuckDB Rollback failed: {e}")
                QMessageBox.critical(self, "Rollback Error", f"Failed to restore model version: {e}")
        else:
            # Legacy YAML config history
            content = version.get("content", "")
            logger.info(f"Rolling back config to {version_id}")
            self.config_manager.update_text(content, source="rollback")
            self.version_manager.create_version(
                content, 
                description=f"Rollback to {version_id[:8]}"
            )
            QMessageBox.information(self, "Rollback Complete", "Configuration restored.")
            
        self.refresh()

