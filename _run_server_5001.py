from app.web_app import create_app

if __name__ == "__main__":
    create_app().run(debug=True, port=5001, use_reloader=False, host="127.0.0.1")
