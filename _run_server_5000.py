from app.web_app import create_app

# Run with project venv so latest code loads:
#   .venv\Scripts\python.exe _run_server_5000.py

if __name__ == "__main__":
    create_app().run(debug=True, port=5000, use_reloader=False, host="127.0.0.1")
