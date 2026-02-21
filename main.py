import cv2
import mediapipe as mp
from flask import Flask

print("OpenCV:", cv2._version_)
print("MediaPipe:", mp._version_)
print("Flask OK")

app = Flask(_name_)

@app.route("/")
def home():
    return "Servidor funcionando"

if _name_ == "_main_":
    app.run(debug=True)
