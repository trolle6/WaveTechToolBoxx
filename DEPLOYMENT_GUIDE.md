# 🚀 Secret Santa Bot Deployment Guide

## 🖥️ **OS-SPECIFIC DEPLOYMENT**

### **Windows Development → Linux Production**

Your setup:
- **Development**: Windows 10/11 (your local machine)
- **Production**: Hetzner server (likely Linux)

## 📋 **DEPLOYMENT STEPS**

### **1. LOCAL DEVELOPMENT (Windows)**

```powershell
# Run in PowerShell on Windows
.\deploy_windows.ps1
```

**Or manually:**
```powershell
# Check Python version
python --version

# Install dependencies
pip install -r requirements.txt

# Create directories
mkdir cogs\archive\backups
mkdir logs

# Set environment variables in config.env
# DISCORD_TOKEN=your_bot_token
# OPENAI_API_KEY=your_openai_key
# DISCORD_MODERATOR_ROLE_ID=your_mod_role_id
```

### **2. PRODUCTION DEPLOYMENT (Linux)**

**On your Hetzner server:**

```bash
# Make scripts executable
chmod +x deploy_linux.sh
chmod +x deploy_cross_platform.py

# Run Linux deployment
./deploy_linux.sh
```

**Or manually:**
```bash
# Check Python version
python3 --version

# Install dependencies
pip3 install -r requirements.txt

# Create directories
mkdir -p cogs/archive/backups
mkdir -p logs

# Set file permissions
chmod +x main.py

# Set environment variables
export DISCORD_TOKEN="your_bot_token"
export OPENAI_API_KEY="your_openai_key"
export DISCORD_MODERATOR_ROLE_ID="your_mod_role_id"

# Start the bot
python3 main.py
```

## 🔧 **COMMON OS-RELATED ISSUES**

### **1. File Permissions**
```bash
# Windows: No concept of executable files
# Linux: Files need execute permission
chmod +x main.py  # Only needed on Linux
```

### **2. Path Separators**
```python
# Windows: Uses backslashes
"C:\\Users\\simon\\PycharmProjects\\WaveTechToolBox"

# Linux: Uses forward slashes
"/home/user/WaveTechToolBox"
```

### **3. Line Endings**
```bash
# Windows: CRLF (\r\n)
# Linux: LF (\n)
# Can cause script execution issues
```

### **4. Command Differences**
```bash
# Windows PowerShell
Get-ChildItem, New-Item, etc.

# Linux Bash
ls, mkdir, chmod, etc.
```

## 🛠️ **CROSS-PLATFORM SOLUTIONS**

### **1. Use Python for Everything**
```python
# Instead of OS-specific commands, use Python
import os
import pathlib

# Create directories (works on both)
Path('cogs/archive/backups').mkdir(parents=True, exist_ok=True)

# Check file permissions (works on both)
os.access('main.py', os.R_OK)
```

### **2. Use the Cross-Platform Deployment Script**
```bash
# Works on both Windows and Linux
python deploy_cross_platform.py
```

### **3. Environment Variables**
```bash
# Windows (PowerShell)
$env:DISCORD_TOKEN="your_token"

# Linux (Bash)
export DISCORD_TOKEN="your_token"

# Or use config.env (works on both)
```

## 🎯 **RECOMMENDED DEPLOYMENT PROCESS**

### **Step 1: Local Testing (Windows)**
```powershell
# Test on Windows first
.\deploy_windows.ps1
python main.py
```

### **Step 2: Production Deployment (Linux)**
```bash
# Deploy to Hetzner server
./deploy_linux.sh
python3 main.py
```

### **Step 3: Verify Deployment**
```bash
# Check if bot is running
ps aux | grep python

# Check logs
tail -f bot.log

# Check if bot is online in Discord
```

## 🔍 **TROUBLESHOOTING**

### **Common Issues:**

1. **"Permission denied" on Linux**
   ```bash
   chmod +x main.py
   ```

2. **"Module not found" on Linux**
   ```bash
   pip3 install -r requirements.txt
   ```

3. **"Python not found" on Linux**
   ```bash
   # Install Python 3.9+
   sudo apt update
   sudo apt install python3.9 python3.9-pip
   ```

4. **"Environment variables not set"**
   ```bash
   # Set in config.env or system environment
   export DISCORD_TOKEN="your_token"
   ```

## 📊 **DEPLOYMENT CHECKLIST**

### **Before Deployment:**
- [ ] Python 3.9+ installed
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Environment variables set
- [ ] Directories created
- [ ] File permissions set (Linux)

### **After Deployment:**
- [ ] Bot starts without errors
- [ ] Commands register properly
- [ ] State file loads correctly
- [ ] Archive directory accessible
- [ ] Bot appears online in Discord

## 🚀 **QUICK START**

### **Windows Development:**
```powershell
.\deploy_windows.ps1
python main.py
```

### **Linux Production:**
```bash
./deploy_linux.sh
python3 main.py
```

## 💡 **PRO TIPS**

1. **Use the cross-platform deployment script** - it handles OS differences automatically
2. **Test locally first** - catch issues before deploying to production
3. **Use environment variables** - easier than hardcoding values
4. **Check logs** - `bot.log` contains detailed information
5. **Use the deployment report** - `deployment_report.json` shows what was checked

## 🎯 **FINAL RESULT**

After following this guide, your Secret Santa bot will work perfectly on both:
- ✅ **Windows development environment**
- ✅ **Linux production server (Hetzner)**

The cross-platform deployment scripts handle all OS differences automatically! 🚀
