import os
import subprocess


def run(user_input):
    os.system(user_input)
    subprocess.call(user_input, shell=True)
