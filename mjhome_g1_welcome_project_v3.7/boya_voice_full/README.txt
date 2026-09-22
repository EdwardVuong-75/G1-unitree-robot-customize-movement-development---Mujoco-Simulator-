BOYA full voice project

Laptop side must run:
  1) ollama serve
  2) C:\mjhome_laptop_agent\.venv\Scripts\Activate.ps1
     python C:\mjhome_laptop_agent\laptop_agent_server.py

Robot test:
  cd ~/mjhome_g1_welcome_project/boya_voice_full
  ./test_boya_once.sh

Robot web:
  cd ~/mjhome_g1_welcome_project/boya_voice_full
  ./start_boya_full.sh

Open browser:
  http://192.168.123.164:5013
