echo '=== MiroFish Status ===' && date
echo
echo '=== Services ==='
systemctl --no-pager --no-legend --plain status mirofish-backend.service xauex.service mirofish-bridge.timer 2>/dev/null | sed -n '1,12p'
echo
# Find active sim
SIM=$(ls /home/bolyki/mirofish-gold-oracle/backend/uploads/simulations/ 2>/dev/null | tail -1)
if [ -n "$SIM" ]; then
  curl -s http://localhost:5001/api/simulation/$SIM/run-status | python3 -c '
import sys,json
d=json.load(sys.stdin)["data"]
print("SIM:", d["simulation_id"])
print("Status:", d["runner_status"], f"{d["progress_percent"]:.0f}%", f"round {d["current_round"]}/{d["total_rounds"]}")
print("Error:", d["error"][:120] if d["error"] else "none")
' 2>/dev/null
fi
echo
echo '=== Signal ===' && cat /var/lib/xauex/cmd.json 2>/dev/null | python3 -m json.tool 2>/dev/null || echo 'No signal yet'
echo
echo '=== Last log milestones ===' && grep -v 'httpcore\|httpx DEBUG\|receive_response\|send_request\|connect_tcp\|close\.\|start_tls\|response_body' /home/bolyki/mirofish-gold-oracle/logs/run.log | tail -5
