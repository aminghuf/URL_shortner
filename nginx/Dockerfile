FROM nginx:1.25-alpine

# Remove default nginx config
RUN rm -f /etc/nginx/conf.d/default.conf

# Copy custom nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Copy SSL certificates
COPY ssl/fullchain.pem /etc/nginx/ssl/fullchain.pem
COPY ssl/privkey.pem /etc/nginx/ssl/privkey.pem
RUN chmod 600 /etc/nginx/ssl/privkey.pem

# Create directory for nginx logs
RUN mkdir -p /var/log/nginx && \
    touch /var/log/nginx/access.log /var/log/nginx/error.log && \
    chmod 640 /var/log/nginx/*.log

# Expose ports
EXPOSE 80 443

# Nginx runs in foreground as PID 1
CMD ["nginx", "-g", "daemon off;"]
