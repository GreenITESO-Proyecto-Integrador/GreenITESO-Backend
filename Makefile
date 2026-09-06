runDev:
	docker build -t greeniteso-backend . && docker run --rm -it -p 8000:8000 -v $(PWD)/kit:/workspace -v $(PWD)/volume/logs:/workspace/logs greeniteso-backend