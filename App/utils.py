import random
import string

def random_filename(ext=".mp4"):
    """Generate random file names like real human devs do"""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(10)) + ext
