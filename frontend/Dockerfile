FROM nginx:alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html /usr/share/nginx/html/
COPY config.js /usr/share/nginx/html/
COPY fontAwesome/ /usr/share/nginx/html/fontAwesome/
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
