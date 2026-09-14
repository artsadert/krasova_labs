run:
	sudo docker compose --env-file ./.env up -d
stop:
	sudo docker compose down
