from flask import Flask, send_file, render_template
from io import BytesIO

app = Flask(__name__)

KEY = 123  # same XOR key you used

def xor_decrypt_from_txt(path: str, key: int) -> bytes:
    with open(path, "r") as f:
        encrypted = [int(b) for b in f.read().split()]
    return bytes(b ^ key for b in encrypted)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/image/<name>")
def image(name):
    if name == "image":
        data = xor_decrypt_from_txt("image.txt", KEY)
    elif name == "nltk":
        data = xor_decrypt_from_txt("nltk.txt", KEY)
    elif name == "one":
        data = xor_decrypt_from_txt("one.txt", KEY)
    elif name == "two":
        data = xor_decrypt_from_txt("two.txt", KEY)
    else:
        return "Nope", 404

    return send_file(
        BytesIO(data),
        mimetype="image/png"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7777, debug=False)
