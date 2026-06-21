<?php
/**
 * Extra Roundcube config (loaded from /var/roundcube/config/).
 *
 * Dovecot and Postfix present self-signed certificates on the internal Docker
 * network, so disable peer verification for the IMAP/SMTP TLS handshakes. This
 * is safe here: the traffic stays on the private compose network and never
 * traverses the public internet.
 */
$ssl_opts = [
    'ssl' => [
        'verify_peer'       => false,
        'verify_peer_name'  => false,
        'allow_self_signed' => true,
    ],
];
$config['imap_conn_options'] = $ssl_opts;
$config['smtp_conn_options'] = $ssl_opts;

// Submit outbound mail to Postfix using the logged-in user's own credentials.
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';

// TLS is terminated upstream (Cloudflare Tunnel / reverse proxy).
$config['use_https'] = false;
