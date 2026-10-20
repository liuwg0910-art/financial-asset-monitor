"""Local browser smoke-test server. Does not change production routing."""
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'backend'))
from main import app
from fastapi.staticfiles import StaticFiles
import uvicorn
app.mount('/',StaticFiles(directory=root/'frontend'/'static',html=True),name='ui-test')
if __name__=='__main__':
    uvicorn.run(app,host='127.0.0.1',port=19192)
