#!/bin/bash

echo "🔄 Stopping existing Odoo processes..."
pkill -f odoo-bin 2>/dev/null
sleep 1

echo "🚀 Starting Odoo Server with auto-reload (--dev=all)..."
cd /home/dgkhoa/Recruitment_Detail/Recruitment_Dev/odoo
../venv/bin/python ./odoo-bin -c ../config/odoo.conf -d recruitment -u recruitment --dev=all
