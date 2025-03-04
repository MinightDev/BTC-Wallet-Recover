#!/bin/bash

echo "Installing Python packages from requirements.txt..."
pip install -r requirements.txt

if [ $? -eq 0 ]; then
    echo "Requirements installed successfully."
else
    echo "Failed to install some or all requirements."
fi

if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    echo "Starting recover.exe..."
    ./recover.exe &
else
    echo "recover.exe can only be executed in a Windows environment."
fi

exit 0
