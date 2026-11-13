// PM2 process config for John Event.
// Start:            pm2 start ecosystem.config.js
// Survive reboots:  pm2 startup   (run the command it prints), then: pm2 save
const path = require("path");

// Python inside the project's virtual environment (Windows uses Scripts\python.exe)
const python = process.platform === "win32"
  ? path.join(__dirname, "venv", "Scripts", "python.exe")
  : path.join(__dirname, "venv", "bin", "python");

module.exports = {
  apps: [
    {
      name: "john-event-bot",
      script: "bot.py",
      interpreter: python,
      cwd: __dirname,
      env: {
        PYTHONUNBUFFERED: "1", // flush logs straight to PM2
      },
      autorestart: true,
      // Wait between restarts, growing on repeated crashes (e.g. Discord or network outage)
      exp_backoff_restart_delay: 5000,
      // A run shorter than this counts as a crash; after max_restarts in a row PM2 gives up
      min_uptime: "30s",
      max_restarts: 20,
      // Give discord.py time to close the gateway connection cleanly on stop/restart
      kill_timeout: 10000,
      watch: false,
    },
  ],
};
