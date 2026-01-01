"""
Health Monitor - Proactive Failure Detection

FEATURES:
- 🔍 Disk space checks (warns before write operations fail)
- 📊 Queue size monitoring (early warnings before capacity)
- ✅ File permission validation
- 🛡️ Data integrity checks
- ⚠️ Resource usage warnings

USAGE:
    from .health_monitor import HealthMonitor
    
    monitor = HealthMonitor(logger)
    
    # Check before critical operation
    if not await monitor.check_disk_space(path, required_mb=10):
        logger.warning("Low disk space - operation may fail")
    
    # Check queue capacity
    if monitor.check_queue_warning(current_size, max_size, warn_threshold=0.8):
        logger.warning("Queue approaching capacity")
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional, Tuple

logger = None  # Will be set by HealthMonitor


class HealthMonitor:
    """
    Proactive health monitoring for early failure detection.
    Detects potential issues before they cause failures.
    """
    
    def __init__(self, logger_instance=None):
        """Initialize health monitor with optional logger"""
        global logger
        logger = logger_instance
    
    def _log_warning(self, message: str):
        """Log warning if logger available"""
        if logger:
            logger.warning(message)
    
    def _log_error(self, message: str):
        """Log error if logger available"""
        if logger:
            logger.error(message)
    
    def check_disk_space(self, path: Path, required_mb: float = 1.0, warn_threshold_mb: float = 50.0) -> Tuple[bool, Optional[str]]:
        """
        Check if there's enough disk space for an operation.
        
        Args:
            path: Path to check disk space for
            required_mb: Minimum MB needed for operation
            warn_threshold_mb: Warn if less than this MB available
        
        Returns:
            (is_safe: bool, warning_message: Optional[str])
        """
        try:
            # Get disk usage for the path's filesystem
            stat = shutil.disk_usage(path)
            free_mb = stat.free / (1024 * 1024)  # Convert bytes to MB
            
            # Check if operation is safe
            if free_mb < required_mb:
                error_msg = f"⚠️ CRITICAL: Only {free_mb:.1f}MB free disk space (need {required_mb:.1f}MB) - operation may fail!"
                self._log_error(error_msg)
                return False, error_msg
            
            # Warn if approaching low disk space
            if free_mb < warn_threshold_mb:
                warning_msg = f"⚠️ Low disk space: {free_mb:.1f}MB available (warn threshold: {warn_threshold_mb:.1f}MB)"
                self._log_warning(warning_msg)
                return True, warning_msg
            
            return True, None
        
        except Exception as e:
            # If we can't check, assume safe (don't block operations)
            self._log_warning(f"Could not check disk space for {path}: {e}")
            return True, None
    
    def check_file_permissions(self, path: Path, check_write: bool = True) -> Tuple[bool, Optional[str]]:
        """
        Check if file operations are possible.
        
        Args:
            path: Path to check
            check_write: If True, check write permissions
        
        Returns:
            (is_safe: bool, warning_message: Optional[str])
        """
        try:
            # Check if parent directory exists and is writable
            parent = path.parent
            if not parent.exists():
                # Try to check if we can create it
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                except (PermissionError, OSError) as e:
                    error_msg = f"⚠️ Cannot create directory {parent}: {e}"
                    self._log_error(error_msg)
                    return False, error_msg
            
            if check_write:
                # Check if we can write to the directory
                if not os.access(parent, os.W_OK):
                    error_msg = f"⚠️ No write permission for {parent}"
                    self._log_error(error_msg)
                    return False, error_msg
                
                # If file exists, check if we can write to it
                if path.exists() and not os.access(path, os.W_OK):
                    error_msg = f"⚠️ No write permission for existing file {path}"
                    self._log_error(error_msg)
                    return False, error_msg
            
            return True, None
        
        except Exception as e:
            self._log_warning(f"Could not check permissions for {path}: {e}")
            return True, None  # Assume safe if check fails
    
    def check_queue_warning(self, current_size: int, max_size: int, warn_threshold: float = 0.8) -> Optional[str]:
        """
        Check if queue is approaching capacity and warn early.
        
        Args:
            current_size: Current queue size
            max_size: Maximum queue capacity
            warn_threshold: Warn when usage exceeds this ratio (0.0-1.0)
        
        Returns:
            Warning message if threshold exceeded, None otherwise
        """
        if max_size <= 0:
            return None
        
        usage_ratio = current_size / max_size
        
        if usage_ratio >= 1.0:
            error_msg = f"⚠️ CRITICAL: Queue at capacity ({current_size}/{max_size}) - new items will be rejected!"
            self._log_error(error_msg)
            return error_msg
        
        if usage_ratio >= warn_threshold:
            warning_msg = f"⚠️ Queue approaching capacity: {current_size}/{max_size} ({usage_ratio*100:.1f}% full)"
            self._log_warning(warning_msg)
            return warning_msg
        
        return None
    
    def check_data_size(self, data_size_mb: float, max_recommended_mb: float = 10.0) -> Optional[str]:
        """
        Warn if data being saved is unusually large (may indicate issues).
        
        Args:
            data_size_mb: Size of data in MB
            max_recommended_mb: Warn if data exceeds this size
        
        Returns:
            Warning message if data is large, None otherwise
        """
        if data_size_mb > max_recommended_mb:
            warning_msg = f"⚠️ Large data size: {data_size_mb:.1f}MB (recommended max: {max_recommended_mb:.1f}MB) - may indicate data bloat"
            self._log_warning(warning_msg)
            return warning_msg
        
        return None
    
    def validate_path_safety(self, path: Path, operation: str = "write") -> Tuple[bool, Optional[str]]:
        """
        Comprehensive path validation before file operations.
        Combines disk space and permission checks.
        
        Args:
            path: Path to validate
            operation: Operation type ("write" or "read")
        
        Returns:
            (is_safe: bool, error_message: Optional[str])
        """
        # Check permissions first (fastest check)
        is_safe, perm_error = self.check_file_permissions(path, check_write=(operation == "write"))
        if not is_safe:
            return False, perm_error
        
        # Check disk space for write operations
        if operation == "write":
            is_safe, disk_error = self.check_disk_space(path, required_mb=1.0, warn_threshold_mb=50.0)
            if not is_safe:
                return False, disk_error
            # Even if safe, return warning if present
            if disk_error:
                return True, disk_error
        
        return True, None

