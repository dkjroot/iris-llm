import json

# Load the config template
config_template = 'config_template.json'
try:
    with open(config_template) as config_template_file:
        settings = json.load(config_template_file)
except FileNotFoundError:
    print("File not found:", config_template)
    exit(1)

created_config = {}

# Iterate each configurable item and ask the user to choose
for config_item in settings['config-items']:
    print("Please choose a value for the", config_item['name'] + ":")
    n = 1
    for option in config_item['available']:
        print(str(n) + ":", option['name'], "    ", option['warning'])
        n += 1
    choice = int(input("Enter a number: "))
    chosen = config_item['available'][choice - 1]
    print("\nYou chose:", chosen['name'], "\n", chosen['notice'], "\n")
    created_config[config_item['name']] = chosen['name']
    for mod_config_item in chosen['module-specific-config']:
        created_config[mod_config_item['name']] = mod_config_item['value']

# Save the config to a file
with open('local-config.json', 'w') as config_file:
    json.dump(created_config, config_file, indent=4)
