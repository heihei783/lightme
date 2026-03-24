import yaml

from utils.path_tool import get_abs_path

def load_configai_config(config_path:str = get_abs_path("config/config_ai.yaml"),encoding:str = "utf-8"):
    with open (config_path ,"r",encoding = encoding) as f:
         return yaml.load(f,Loader = yaml.FullLoader)
    
config_ai = load_configai_config()





