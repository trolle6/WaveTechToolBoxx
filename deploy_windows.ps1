# Secret Santa Bot Deployment Script for Windows
# Run this in PowerShell on Windows

Write-Host "🚀 Secret Santa Bot Deployment (Windows)" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green

# Check if Python is available
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✅ Python found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ Python not found. Please install Python 3.10+ (required for disnake 2.12 / Discord voice)" -ForegroundColor Red
    exit 1
}

# Check if pip is available
try {
    $pipVersion = pip --version 2>&1
    Write-Host "✅ pip found: $pipVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ pip not found. Please install pip" -ForegroundColor Red
    exit 1
}

# Create required directories
Write-Host "📁 Creating directories..." -ForegroundColor Yellow
$directories = @(
    "cogs\archive",
    "cogs\archive\backups",
    "logs"
)

foreach ($dir in $directories) {
    if (!(Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "✅ Created: $dir" -ForegroundColor Green
    } else {
        Write-Host "✅ Exists: $dir" -ForegroundColor Green
    }
}

# Check critical files
Write-Host "🔍 Checking critical files..." -ForegroundColor Yellow
$criticalFiles = @("main.py", "cogs\SecretSanta_cog.py")

foreach ($file in $criticalFiles) {
    if (Test-Path $file) {
        Write-Host "✅ Found: $file" -ForegroundColor Green
    } else {
        Write-Host "❌ Missing: $file" -ForegroundColor Red
        exit 1
    }
}

# Install dependencies
Write-Host "📦 Installing dependencies..." -ForegroundColor Yellow
try {
    if (Test-Path "requirements.txt") {
        pip install -r requirements.txt
        Write-Host "✅ Dependencies installed from requirements.txt" -ForegroundColor Green
    } else {
        # Install core dependencies
        pip install disnake>=2.9.0 aiohttp>=3.8.0
        Write-Host "✅ Core dependencies installed" -ForegroundColor Green
    }
} catch {
    Write-Host "❌ Failed to install dependencies: $_" -ForegroundColor Red
    exit 1
}

# Check environment variables
Write-Host "🔍 Checking environment variables..." -ForegroundColor Yellow
$requiredVars = @("DISCORD_TOKEN")
$optionalVars = @("OPENAI_API_KEY", "DISCORD_MODERATOR_ROLE_ID")

$missingRequired = @()
foreach ($var in $requiredVars) {
    if (![Environment]::GetEnvironmentVariable($var)) {
        $missingRequired += $var
    }
}

if ($missingRequired.Count -gt 0) {
    Write-Host "❌ Missing required environment variables: $($missingRequired -join ', ')" -ForegroundColor Red
    Write-Host "💡 Set them in config.env or system environment" -ForegroundColor Yellow
    exit 1
}

Write-Host "✅ Required environment variables set" -ForegroundColor Green

foreach ($var in $optionalVars) {
    if ([Environment]::GetEnvironmentVariable($var)) {
        Write-Host "✅ $var set" -ForegroundColor Green
    } else {
        Write-Host "⚠️ $var not set (optional)" -ForegroundColor Yellow
    }
}

# Run the cross-platform deployment script
Write-Host "🔍 Running cross-platform checks..." -ForegroundColor Yellow
python deploy_cross_platform.py

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ All checks passed!" -ForegroundColor Green
    Write-Host ""
    Write-Host "🚀 To start the bot:" -ForegroundColor Cyan
    Write-Host "   python main.py" -ForegroundColor White
    Write-Host ""
    Write-Host "📋 Deployment completed successfully!" -ForegroundColor Green
} else {
    Write-Host "❌ Some checks failed. Fix issues before deployment." -ForegroundColor Red
    exit 1
}
