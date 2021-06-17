FROM ubuntu:18.04

RUN apt-get update -y && apt install -y python3-pip less

COPY . /r53cli

WORKDIR /r53cli

RUN pip3 install -r requirements.txt

RUN chmod 755 r53_record_cli.py

CMD "/bin/bash"
