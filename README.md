# QuickSync4Linux
It is annoying that companies always forget to implement their software for the most important operating system. This is a minimal implementation of the Gigaset QuickSync software for Linux.

The communication with the device is based on AT commands over a USB/Bluetooth serial port. For file transfer, the device is set into Obex mode.

## Hardware Setup
Make sure your user is in the dialout group in order to access the serial port.
```
sudo usermod -aG dialout <username>
# logout and login again to apply group membership
```

## Usage
First, find out the correct serial port device. After connecting, a serial port like `/dev/ttyACM0` (USB on Linux), `/dev/usbmodem` (USB on macOS) or `/dev/rfcomm0` (Bluetooth) should appear. `/dev/ttyACM0` is used by default. If your device differs, you can use the `--device` parameter. More information regarding Bluetooth serial connections can be found [here](https://gist.github.com/0/c73e2557d875446b9603).

Then, you can use one of the following commands:
```
# read device metadata
./quicksync.py info

# read device contacts and print VCF to stdout (use --file to store it into a file instead)
./quicksync.py getcontacts

# create a new contact on device from file
./quicksync.py putcontact --file mycontact.vcf

# start a call
./quicksync.py dial 1234567890
```

For debug purposes and reporting issues, please start the script with the `-v` parameter and have a look at the serial communication.

## Tested Devices
Please let me know if you tested this script with another device (regardless of whether it was successful or not).

- Gigaset S700 H PRO (USB + Bluetooth working)
- Gigaset S68H (Bluetooth working, no USB port)

## Support
If you like this project please consider making a donation using the sponsor button on [GitHub](https://github.com/schorschii/QuickSync4Linux) to support further development. If your financial resources do not allow this, you can at least leave a star for the Github repo.

Furthermore, you can hire me for commercial support or adjustments and support for new devices. Please [contact me](https://georg-sieber.de/?page=impressum) if you are interested.
