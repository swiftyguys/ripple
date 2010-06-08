#!/usr/bin/env perl

use 5.010;

use warnings;
use strict;

use URI::Escape;
use Crypt::OpenSSL::RSA;
use CGI::Carp qw(fatalsToBrowser);
use Net::OAuth;
use Data::Random qw(rand_chars);
use LWP::UserAgent;
use CGI ();
use JSON qw(decode_json encode_json);
use HTML::Entities;
use Date::Format;
use Data::Dumper;

my $consumer_key     = "anonymous";
my $consumer_secret  = "anonymous";

my $scope = q{http://wave.googleusercontent.com/api/rpc};

my $oa_req_uri    = q{https://www.google.com/accounts/OAuthGetRequestToken?scope=}.uri_escape($scope);
my $oa_auth_uri   = q{https://www.google.com/accounts/OAuthAuthorizeToken};
my $oa_access_uri = q{https://www.google.com/accounts/OAuthGetAccessToken};

my $rpc_uri = q{https://www-opensocial.googleusercontent.com/api/rpc};


my $base_uri = "http://junai/ripple/ripple.cgi";

my $q = CGI->new;

my $LOCAL = 0;
$LOCAL = 1 if $q->param("l");

if (!$LOCAL) {
    if ($q->param("s") eq "callback") {
        do_callback();
    }
    elsif ($q->param("s") eq "logout") {
        do_logout();
    }
    elsif ($q->cookie("token") && $q->cookie("secret")) {
        do_wave();
    }
    else {
        do_login();
    }
}

else {
    do_wave();
}

exit 0;

sub do_login {
    my $oa_req = Net::OAuth->request("request token")->new(
        _default_request_params(),
        request_url => $oa_req_uri,
        extra_params => {
            scope => $scope,
        },
    );
    $oa_req->sign;

    my $ua = LWP::UserAgent->new;
    my $res = $ua->get($oa_req->to_url);

    if (!$res->is_success) {
        die "could not get request token: ".$res->status_line."\n".$res->content;
    }

    my $oa_res = Net::OAuth->response("request token")->from_post_body($res->content);

    $oa_req = Net::OAuth->request("user auth")->new(
        token        => $oa_res->token,
        callback     => _build_internal_uri(s => 'callback'),
    );

    my $secret_cookie = $q->cookie(-name => "secret", -value => $oa_res->token_secret);

    print $q->redirect(-uri => $oa_req->to_url($oa_auth_uri), -cookie => [$secret_cookie]);
}

sub do_callback {
    my $oa_res = Net::OAuth->response("user auth")->from_hash({$q->Vars});

    my $oa_req = Net::OAuth->request("access token")->new(
        _default_request_params(),
        request_url  => $oa_access_uri,
        token        => $oa_res->token,
        token_secret => $q->cookie("secret"),
    );
    $oa_req->sign;

    my $ua = LWP::UserAgent->new;
    my $res = $ua->get($oa_req->to_url);

    if (!$res->is_success) {
        die "could not get access token: ".$res->status_line."\n".$res->content;
    }

    $oa_res = Net::OAuth->response("access token")->from_post_body($res->content);

    my $token_cookie = $q->cookie(-name => "token", -value => $oa_res->token);
    my $secret_cookie = $q->cookie(-name => "secret", -value => $oa_res->token_secret);

    print $q->redirect(-uri => _build_internal_uri(), -cookie => [$token_cookie, $secret_cookie]);
}

sub do_logout {
    my $token_cookie = $q->cookie(-name => "token", -value => "");
    my $secret_cookie = $q->cookie(-name => "secret", -value => "");

    print $q->redirect(-uri => _build_internal_uri(), -cookie => [$token_cookie, $secret_cookie]);
}

sub do_wave {
    my %action_handler = (
        inbox  => \&action_inbox,
        search => \&action_search,
        read   => \&action_read,
        test   => \&action_test,
    );

    my $out = '';

    my $action = $q->param("a") || "inbox";
    if (exists $action_handler{$action}) {
        $out = $action_handler{$action}->();
    }

    if (defined $out) {
        print $q->header("text/html");

        print _html_header();

        print _form_wrap(
            [qw(submit a inbox)],
            [qw(submit a test)],
            [qw(submit s logout)],
        );

        print _form_wrap(
            [qw(text q), $q->param("q")],
            [qw(submit a search)],
        );

        print $out;

        print _html_footer();
    }
}

sub action_inbox {
    $q->param("q", "in:inbox");
    return action_search();
}

sub action_search {
    my $data = _wave_request({
        id     => "op1",
        method => "wave.robot.search",
        params => {
            query => $q->param("q"),
        },
    });

    my $out = '';
    for my $digest (@{$data->{data}->{searchResults}->{digests}}) {
        $out .=
            q{<a href='}._build_internal_uri(a => 'read', w => $digest->{waveId}).q{'>}.
                q{<b>}.encode_entities($digest->{title}).q{</b>}.
                q{ }.encode_entities($digest->{snippet}).
            q{</a><br />};
    }

    return $out;
}

sub action_read {
    my $wave_id = $q->param("w"); $wave_id =~ s/ /+/g;
    my ($wavelet_id) = $wave_id =~ m/^([^!]+)/;
    $wavelet_id .= q{!conv+root};

    my $data;
    if ($LOCAL) {
        $data = do {
            no strict;
            eval do { local (@ARGV, $/) = ('/home/rob/code/wave/ripple/waves/wave_'.$wave_id); <> };
        };
    }

    else {
        $data = _wave_request({
            id     => "read1",
            method => "wave.robot.fetchWave",
            params => {
                waveId    => $wave_id,
                waveletId => $wavelet_id,
            },
        });
    }

    my $out;
    if (my $root_blip_id = $data->{data}->{waveletData}->{rootBlipId}) {
        $out = _render_blip($wave_id, $wavelet_id, $root_blip_id, $data->{data}->{blips});
    }
    else {
        $out = '<p>no root blip?</p>';
    }

    $out .= q{<pre>}.Dumper($data).q{</pre>};

    return $out;
}

# waveletdata
# {
#   'waveletId': 'eatenbyagrue.org!conv+root',
#   'waveId': 'eatenbyagrue.org!TBD_0x4c8cad8a',
#   'rootBlipId': 'TBD_eatenbyagrue.org!conv+root_0x401c33cd',
#   'participants': set(['rob@eatenbyagrue.org'])
# }
# blipdata
# {
#   'waveletId': 'eatenbyagrue.org!conv+root',
#   'blipId': 'TBD_eatenbyagrue.org!conv+root_0x401c33cd',
#   'waveId': 'eatenbyagrue.org!TBD_0x4c8cad8a',
#   'content': '',
#   'parentBlipId': None}
# }
#
# operation
# {
#   'id': 'op1'
#   'method': 'robot.createWavelet',
#   'params': {
#     'waveId': 'eatenbyagrue.org!TBD_0x16348be1'
#     'waveletId': 'eatenbyagrue.org!conv+root', 
#     'waveletData': {
#       'waveletId': 'eatenbyagrue.org!conv+root', 
#       'waveId': 'eatenbyagrue.org!TBD_0x16348be1',
#       'rootBlipId': 'TBD_eatenbyagrue.org!conv+root_0x4b666aa8',
#       'participants': [
#         'rob@eatenbyagrue.org'
#       ]
#     },
#   },
# }

sub action_test {
    my $wave_id = sprintf q{eatenbyagrue.org!TBD_0x%08x}, int rand 4294967296;
    my $wavelet_id = q{eatenbyagrue.org!conv+root};
    my $root_blip_id = sprintf q{TBD_%s_0x%08x}, $wavelet_id, int rand 4294967296;

    my $data = _wave_request({
        id => "test1",
        method => "wave.robot.createWavelet",
        params => {
            waveId => $wave_id,
            waveletId => $wavelet_id,
            waveletData => {
                waveId => $wave_id,
                waveletId => $wavelet_id,
                rootBlipId => $root_blip_id,
                participants => [
                    q{rob@eatenbyagrue.org},
                ],
            },
        },
    });

    return q{<pre>}.Dumper($data).q{</pre>};

    return;
}

=pod
sub action_test {
    my $data = _wave_request({
        id => "test1",
        method => "wave.robot.fetchWave",
        params => {
            waveId => q{ga-staff-dev.monash.edu!w+Zgx9msiJA},
            waveletId => q{ga-staff-dev.monash.edu!conv+root},
        },
    });

    print $q->header("text/plain");
    print Dumper $data;
}
=cut

sub _wave_request {
    my ($rpc) = @_;

    my $oa_req = Net::OAuth->request("protected resource")->new(
        _default_request_params("POST"),
        request_url  => $rpc_uri,
        token        => $q->cookie("token"),
        token_secret => $q->cookie("secret"),
    );
    $oa_req->sign;

    my $ua = LWP::UserAgent->new;
    my $res = $ua->post($oa_req->to_url, Content_type => "application/json", Content => encode_json($rpc));

    if (!$res->is_success) {
        die "could not do rpc call: ".$res->status_line."\n".$res->content;
    }

    return decode_json($res->content);
}

sub _default_request_params {
    my ($method) = @_;
    $method //= "GET";

    return (
        consumer_key     => $consumer_key,
        consumer_secret  => $consumer_secret,
        request_method   => $method,
        signature_method => "HMAC-SHA1",
        timestamp        => time,
        nonce            => join('', rand_chars(size => 16, set => "alphanumeric"))
    );
}

sub _html_header {
    return <<HTML_HEADER
<html>
<head>
<title>ripple</title>
<style type="text/css">
body {
    font-family: sans-serif;
    font-size: smaller;
}

/* root blip */
body > div.blip {
    margin: 5px;
    padding: 5px;
    background-color: #99ff99;
}

/* normal blip */
div.blip > div.blip {
    margin: 5px;
    padding: 5px;
    background-color: #9999ff;
}

/* inline blip */
div.blip-content > div.blip {
    margin: 5px;
    padding: 5px;
    background-color: #ff99ff;
}

div.blip-debug {
    float: right;
    margin-right: 5px;
    padding: 2px;
    border: solid black 1px;
    background-color: #ffff99;
    font-family: monospace;
}

div.blip-content {
    padding: 5px;
    background-color: white;
    border: solid black 1px;
}
div.blip-content h1 {
    display: inline;
}

div.blip-reply {
    padding: 5px;
}
div.blip-reply textarea {
    width: 100%;
}

div.image {
    border: dashed #666666 3px;
    background-color: #ffff99;
    font-family: monospace;
    padding: 2px;
}

div.gadget {
    border: dashed #666666 3px;
    background-color: #ffff99;
    font-family: monospace;
    padding: 2px;
}
</style>
</head>
<body>
HTML_HEADER
;
}

sub _html_footer {
    return <<HTML_FOOTER
</body>
</html>
HTML_FOOTER
;
}

sub _build_internal_uri {
    my (%args) = @_;

    $args{l} = 1 if $LOCAL and !exists $args{l};

    return $base_uri . (keys %args ? q{?}.join(q{&}, map { "$_=$args{$_}" } keys %args) : q{});
}

sub _form_wrap {
    my @elements = grep { ref $_ eq "ARRAY" } @_;
    my ($opts)   = grep { ref $_ eq "HASH"  } @_;

    $opts //= {};
    $opts->{'method'} ||= 'get';

    my $out = q{<form action='}._build_internal_uri().q{' method='}.$opts->{method}.q{'};
    $out .= q{ style='}.$opts->{style}.q{'} if exists $opts->{style};
    $out .= q{>};

    $out .= q{<input type='hidden' name='l' value='1' />} if $LOCAL;

    for my $element (@elements) {
        my ($type, $name, $value) = @$element;
        $value ||= '';
        if ($type eq 'textarea') {
            $out .= q{<textarea name='}.$name.q{'>}.$value.q{</textarea>};
        }
        else {
            $out .= q{<input type='}.$type.q{' name='}.$name.q{' value='}.$value.q{' />};
        }
    }

    $out .= q{</form>}
    ;
}

sub _render_blip {
    my ($wave_id, $wavelet_id, $blip_id, $blips, $distance) = @_;
    $distance ||= 0;

    my $blip = $blips->{$blip_id};

    my %children = map { $_ => 1 } @{$blip->{childBlipIds}};
    delete $children{$_} for map { $_->{type} eq "INLINE_BLIP" ? $_->{properties}->{id} : () } values %{$blip->{elements}};

    my $out = '';
    $out .=
        q{<div class='blip' id='}.$blip_id.q{'>}.
        q{<b>}.$blip->{creator}.q{</b>}.
        time2str(q{ at <b>%l:%M%P</b> on <b>%e %B</b>}, $blip->{lastModifiedTime}/1000);

    my @contributors = grep { $_ ne $blip->{creator} } @{$blip->{contributors}};
    if (@contributors) {
        $out .=
            q{ (and }.
            join (q{, }, @contributors).
            q{)};
    }

    $out .= 
        q{<div class='blip-debug'>}.
            q{blip: }.$blip_id.q{<br />}.
            q{parent: }.$blip->{parentBlipId}.q{<br />}.
        q{</div>};

    $out .=
        q{<div class='blip-content'>};

    # 0 - end annotation
    # 1 - element
    # 2 - start annotation

    my $blipmeta = {};
    for my $pos (keys %{$blip->{elements}}) {
        push @{$blipmeta->{$pos}}, [ 1, $blip->{elements}->{$pos} ];
    }
    for my $annotation (@{$blip->{annotations}}) {
        push @{$blipmeta->{$annotation->{range}->{start}}}, [ 2, $annotation ];
        push @{$blipmeta->{$annotation->{range}->{end}}},   [ 0, $annotation ];
    }

    if (!keys %$blipmeta) {
        $out .= $blip->content;
    }

    else {
        my @positions = sort { $a <=> $b } keys %$blipmeta;
        for my $i (0 .. $#positions) {
            my $position = $positions[$i];

            my (%start, %end, %point);

            for my $meta (sort { $a <=> $b } @{$blipmeta->{$position}}) {
                my ($type, $thing) = @$meta;

                if ($type == 2) {
                    given ($thing->{name}) {
                        when ("conv/title") {
                            push @{$start{elements}}, [qw(h1)];
                        }

                        when (m{^link/(?:manual|auto)}) {
                            next if $start{link};
                            $start{link} = $thing->{value};
                        }
                        when ("link/wave") {
                            next if $start{link};
                            $start{link} = _build_internal_uri(a => 'read', w => $thing->{value});
                        }

                        when (m{^style/(.*)}) {
                            my $name = $1;
                            $name =~ s/([A-Z])/q{-}.lc($1)/e;
                            $start{style}->{$name} = $thing->{value};
                        }

                        default {
                            #$out .= q{<span style='background-color: #000066; color: #ffffff;'>}.$thing->{name}.q{</span>};
                        }
                    }

                }

                elsif ($type == 0) {
                    given ($thing->{name}) {
                        when ("conv/title") {
                            push @{$end{elements}}, qw(h1);
                        }

                        when (m{^link/}) {
                            $end{link} = 1;
                        }

                        when (m{^style/}) {
                            $end{style} = 1;
                        }

                        default {
                            #$out .= q{<span style='background-color: #006600; color: #ffffff;'>}.$thing->{name}.q{</span>};
                        }
                    }

                }

                elsif ($type == 1) {
                    given ($thing->{type}) {
                        when ("LINE") {
                            push @{$point{elements}}, [q{br}] if $positions[$i] != 0;
                        }
                        when ("INLINE_BLIP") {
                            push @{$point{blips}}, $thing->{properties}->{id};
                        }
                        when ("IMAGE") {
                            push @{$point{images}}, $thing->{properties};
                        }
                        when ("GADGET") {
                            push @{$point{gadgets}}, $thing->{properties};
                        }
                        default {
                            #$out .= q{<span style='background-color: #660000; color: #ffffff;'>}.$thing->{type}.q{</span>};
                        }
                    }
                }
            }

            # range start
            for my $elem (@{$start{elements}}) {
                my ($tag, %attrs) = @$elem;
                $out .= q{<}.$tag;
                $out .= q{ }.$_.q{='}.$attrs{$_}.q{'} for keys %attrs;
                $out .= q{>};
            }

            if ($start{link}) {
                $out .= q{<a href='}.$start{link}.q{'>};
            }

            if (keys %{$start{style}}) {
                $out .= q{<span style='};
                $out .= $_.q{: }.$start{style}->{$_}.q{;} for keys %{$start{style}};
                $out .= q{'>};
            }

            # points
            for my $elem (@{$point{elements}}) {
                my ($tag, %attrs) = @$elem;
                $out .= q{<}.$tag;
                $out .= q{ }.$_.q{='}.$attrs{$_}.q{'} for keys %attrs;
                $out .= q{ />};
            }

            $out .= _render_blip($wave_id, $wavelet_id, $_, $blips, 1) for @{$point{blips}};
            $out .= _render_image($_) for @{$point{images}};
            $out .= _render_gadget($_) for @{$point{gadgets}};

            # range end
            $out .= q{</span>} if $end{style};
            $out .= q{</a>} if $end{link};
            $out .= q{</}.$_.q{>} for @{$end{elements}};

            #$out .= q{<span style='background-color: #000000; color: #ffffff'>}.$position.q{ - }.($i < $#positions ? $positions[$i+1] : length $blip->{content}).q{</span>};

            # blip content
            $out .= encode_entities(substr ($blip->{content},
                                            $position,
                                            ($i < $#positions ? $positions[$i+1] : length $blip->{content}) - $position));
        }
    }

    $out .= q{</div>};

    $out .= q{</div>} if $distance != 1; # root or thread blip

    if (@{$blip->{childBlipIds}}) {
        for my $child_blip_id (grep { exists $children{$_} } @{$blip->{childBlipIds}}) {
            $out .= _render_blip($wave_id, $wavelet_id, $child_blip_id, $blips, $distance+1);
        }
    }

    $out .= _reply_textarea($wave_id, $wavelet_id, $blip_id) if $distance <= 1;

    $out .= q{</div>} if $distance == 1; # top reply blip

    return $out;
}

sub _reply_textarea {
    my ($wave_id, $wavelet_id, $blip_id) = @_;

    return
        q{<div class='blip-reply'>}.
            _form_wrap( { method => 'post', style  => 'display: inline;' },
               [qw(hidden w),  $wave_id],
               [qw(hidden wl), $wavelet_id],
               [qw(hidden b),  $blip_id],
               [qw(textarea r)],
               [qw(submit a reply)], 
            ).
        q{</div>}
    ;
}

sub _render_image {
    my ($properties) = @_;

    my $out =
        q{<div class='image'>}.
            q{IMAGE: [}.encode_entities($properties->{attachmentId}).q{]};
    
    $out .= q{ }.encode_entities($properties->{caption}) if exists $properties->{caption};
    $out .= q{</div>};

    return $out;
}

sub _render_gadget {
    my ($properties) = @_;

    return
        q{<div class='gadget'>}.
            q{GADGET: }.encode_entities($properties->{url}).
        q{</div>};
}
