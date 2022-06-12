
run:
	docker build -t sig .
	docker run -e SIG_KEY -e SIG_FILE -e BAR_FILE -it -v ${PWD}:/root/ sig ./run.sh


