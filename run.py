from denzo import create_app

app = create_app()

if __name__ == "__main__":
    print("DENZO-SEO starting on http://0.0.0.0:5055")
    app.run(debug=False, host="127.0.0.1", port=5055, threaded=True)
