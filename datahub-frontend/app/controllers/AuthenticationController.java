package controllers;

import client.AuthServiceClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.linkedin.common.urn.CorpuserUrn;
import com.linkedin.common.urn.Urn;
import com.typesafe.config.Config;
import java.io.UnsupportedEncodingException;
import java.net.URLEncoder;
import java.util.Optional;
import org.apache.commons.lang3.StringUtils;
import org.pac4j.core.client.Client;
import org.pac4j.core.context.session.SessionStore;
import org.pac4j.core.exception.HttpAction;
import org.pac4j.play.PlayWebContext;
import org.pac4j.play.http.PlayHttpActionAdapter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import play.libs.Json;
import play.mvc.Controller;
import play.mvc.Http;
import play.mvc.Result;
import auth.AuthUtils;
import auth.JAASConfigs;
import auth.NativeAuthenticationConfigs;
import auth.sso.SsoManager;
import security.AuthenticationManager;

import javax.annotation.Nonnull;
import javax.inject.Inject;

import java.time.Duration;
import java.time.temporal.ChronoUnit;

import static auth.AuthUtils.*;
import static org.pac4j.core.client.IndirectClient.*;


// TODO add logging.
public class AuthenticationController extends Controller {

    private static final String AUTH_REDIRECT_URI_PARAM = "redirect_uri";

    private final Logger _logger = LoggerFactory.getLogger(AuthenticationController.class.getName());
    private final Config _configs;
    private final JAASConfigs _jaasConfigs;
    private final NativeAuthenticationConfigs _nativeAuthenticationConfigs;

    @Inject
    private org.pac4j.core.config.Config _ssoConfig;

    @Inject
    private SessionStore _playSessionStore;

    @Inject
    private SsoManager _ssoManager;

    @Inject
    AuthServiceClient _authClient;

    @Inject
    public AuthenticationController(@Nonnull Config configs) {
        _configs = configs;
        _jaasConfigs = new JAASConfigs(configs);
        _nativeAuthenticationConfigs = new NativeAuthenticationConfigs(configs);
    }

    /**
     * Route used to perform authentication, or redirect to log in if authentication fails.
     *
     * If indirect SSO (eg. oidc) is configured, this route will redirect to the identity provider (Indirect auth).
     * If not, we will fallback to the default username / password login experience (Direct auth).
     */
    @Nonnull
    public Result authenticate() {

        // TODO: Call getAuthenticatedUser and then generate a session cookie for the UI if the user is authenticated.

        final Optional<String> maybeRedirectPath = Optional.ofNullable(ctx().request().getQueryString(AUTH_REDIRECT_URI_PARAM));
        final String redirectPath = maybeRedirectPath.orElse("/");

        if (AuthUtils.hasValidSessionCookie(ctx())) {
            return redirect(redirectPath);
        }

        // 1. If SSO is enabled, redirect to IdP if not authenticated.
        if (_ssoManager.isSsoEnabled()) {
            return redirectToIdentityProvider();
        }

        // 2. If either JAAS auth or Native auth is enabled, fallback to it
        if (_jaasConfigs.isJAASEnabled() || _nativeAuthenticationConfigs.isNativeAuthenticationEnabled()) {
            return redirect(
                LOGIN_ROUTE + String.format("?%s=%s", AUTH_REDIRECT_URI_PARAM, encodeRedirectUri(redirectPath)));
        }

        // 3. If no auth enabled, fallback to using default user account & redirect.
        // Generate GMS session token, TODO:
        final String accessToken = _authClient.generateSessionTokenForUser(DEFAULT_ACTOR_URN.getId());
        session().put(ACCESS_TOKEN, accessToken);
        session().put(ACTOR, DEFAULT_ACTOR_URN.toString());
        return redirect(redirectPath).withCookies(createActorCookie(DEFAULT_ACTOR_URN.toString(), _configs.hasPath(SESSION_TTL_CONFIG_PATH)
                ? _configs.getInt(SESSION_TTL_CONFIG_PATH)
                : DEFAULT_SESSION_TTL_HOURS));
    }

    /**
     * Log in a user based on a username + password.
     *
     * TODO: Implement built-in support for LDAP auth. Currently dummy jaas authentication is the default.
     */
    @Nonnull
    public Result logIn() {
        boolean jaasEnabled = _jaasConfigs.isJAASEnabled();
        _logger.debug(String.format("Jaas authentication enabled: %b", jaasEnabled));
        boolean nativeAuthenticationEnabled = _nativeAuthenticationConfigs.isNativeAuthenticationEnabled();
        _logger.debug(String.format("Native authentication enabled: %b", nativeAuthenticationEnabled));
        boolean noAuthEnabled = !jaasEnabled && !nativeAuthenticationEnabled;
        if (noAuthEnabled) {
            String message = "Neither JAAS nor native authentication is enabled on the server.";
            final ObjectNode error = Json.newObject();
            error.put("message", message);
            return badRequest(error);
        }

        final JsonNode json = request().body().asJson();
        final String username = json.findPath(USER_NAME).textValue();
        final String password = json.findPath(PASSWORD).textValue();

        if (StringUtils.isBlank(username)) {
            JsonNode invalidCredsJson = Json.newObject().put("message", "User name must not be empty.");
            return badRequest(invalidCredsJson);
        }

        ctx().session().clear();

        JsonNode invalidCredsJson = Json.newObject().put("message", "Invalid Credentials");
        boolean loginSucceeded = tryLogin(username, password);

        if (!loginSucceeded) {
            return badRequest(invalidCredsJson);
        }

        final Urn actorUrn = new CorpuserUrn(username);
        final String accessToken = _authClient.generateSessionTokenForUser(actorUrn.getId());
        ctx().session().put(ACTOR, actorUrn.toString());
        ctx().session().put(ACCESS_TOKEN, accessToken);
        return ok().withCookies(Http.Cookie.builder(ACTOR, actorUrn.toString())
            .withHttpOnly(false)
            .withMaxAge(Duration.of(30, ChronoUnit.DAYS))
            .build());
    }

    /**
     * Sign up a native user based on a name, email, title, and password. The invite token must match the global invite
     * token stored for the DataHub instance.
     *
     */
    @Nonnull
    public Result signUp() {
        boolean nativeAuthenticationEnabled = _nativeAuthenticationConfigs.isNativeAuthenticationEnabled();
        _logger.debug(String.format("Native authentication enabled: %b", nativeAuthenticationEnabled));
        if (!nativeAuthenticationEnabled) {
            String message = "Native authentication is not enabled on the server.";
            final ObjectNode error = Json.newObject();
            error.put("message", message);
            return badRequest(error);
        }

        final JsonNode json = request().body().asJson();
        final String fullName = json.findPath(FULL_NAME).textValue();
        final String email = json.findPath(EMAIL).textValue();
        final String title = json.findPath(TITLE).textValue();
        final String password = json.findPath(PASSWORD).textValue();
        final String inviteToken = json.findPath(INVITE_TOKEN).textValue();

        if (StringUtils.isBlank(fullName)) {
            JsonNode invalidCredsJson = Json.newObject().put("message", "Full name must not be empty.");
            return badRequest(invalidCredsJson);
        }

        if (StringUtils.isBlank(email)) {
            JsonNode invalidCredsJson = Json.newObject().put("message", "Email must not be empty.");
            return badRequest(invalidCredsJson);
        }

        if (StringUtils.isBlank(password)) {
            JsonNode invalidCredsJson = Json.newObject().put("message", "Password must not be empty.");
            return badRequest(invalidCredsJson);
        }

        if (StringUtils.isBlank(title)) {
            JsonNode invalidCredsJson = Json.newObject().put("message", "Title must not be empty.");
            return badRequest(invalidCredsJson);
        }

        if (StringUtils.isBlank(inviteToken)) {
            JsonNode invalidCredsJson = Json.newObject().put("message", "Invite token must not be empty.");
            return badRequest(invalidCredsJson);
        }

        ctx().session().clear();

        final Urn userUrn = new CorpuserUrn(email);
        final String userUrnString = userUrn.toString();
        boolean isNativeUserCreated = _authClient.signUp(userUrnString, fullName, email, title, password, inviteToken);
        final String accessToken = _authClient.generateSessionTokenForUser(userUrn.getId());
        ctx().session().put(ACTOR, userUrnString);
        ctx().session().put(ACCESS_TOKEN, accessToken);
        return ok().withCookies(Http.Cookie.builder(ACTOR, userUrnString)
            .withHttpOnly(false)
            .withMaxAge(Duration.of(30, ChronoUnit.DAYS))
            .build());
    }

    /**
     * Create a native user based on a name, email, and password.
     *
     */
    @Nonnull
    public Result resetNativeUserCredentials() {
        boolean nativeAuthenticationEnabled = _nativeAuthenticationConfigs.isNativeAuthenticationEnabled();
        _logger.debug(String.format("Native authentication enabled: %b", nativeAuthenticationEnabled));
        if (!nativeAuthenticationEnabled) {
            String message = "Native authentication is not enabled on the server.";
            final ObjectNode error = Json.newObject();
            error.put("message", message);
            return badRequest(error);
        }

        final JsonNode json = request().body().asJson();
        final String email = json.findPath(EMAIL).textValue();
        final String password = json.findPath(PASSWORD).textValue();
        final String resetToken = json.findPath(RESET_TOKEN).textValue();

        if (StringUtils.isBlank(email)) {
            JsonNode invalidCredsJson = Json.newObject().put("message", "Email must not be empty.");
            return badRequest(invalidCredsJson);
        }

        if (StringUtils.isBlank(password)) {
            JsonNode invalidCredsJson = Json.newObject().put("message", "Password must not be empty.");
            return badRequest(invalidCredsJson);
        }

        if (StringUtils.isBlank(resetToken)) {
            JsonNode invalidCredsJson = Json.newObject().put("message", "Reset token must not be empty.");
            return badRequest(invalidCredsJson);
        }

        ctx().session().clear();

        final Urn userUrn = new CorpuserUrn(email);
        final String userUrnString = userUrn.toString();
        boolean areNativeUserCredentialsReset =
            _authClient.resetNativeUserCredentials(userUrnString, password, resetToken);
        _logger.debug(String.format("Are native user credentials reset: %b", areNativeUserCredentialsReset));
        final String accessToken = _authClient.generateSessionTokenForUser(userUrn.getId());
        ctx().session().put(ACTOR, userUrnString);
        ctx().session().put(ACCESS_TOKEN, accessToken);
        return ok().withCookies(Http.Cookie.builder(ACTOR, userUrnString)
            .withHttpOnly(false)
            .withMaxAge(Duration.of(30, ChronoUnit.DAYS))
            .build());
    }

    private Result redirectToIdentityProvider() {
        final PlayWebContext playWebContext = new PlayWebContext(ctx(), _playSessionStore);
        final Client<?, ?> client = _ssoManager.getSsoProvider().client();

        // This is to prevent previous login attempts from being cached.
        // We replicate the logic here, which is buried in the Pac4j client.
        if (_playSessionStore.get(playWebContext, client.getName() + ATTEMPTED_AUTHENTICATION_SUFFIX) != null) {
            _logger.debug("Found previous login attempt. Removing it manually to prevent unexpected errors.");
            _playSessionStore.set(playWebContext, client.getName() + ATTEMPTED_AUTHENTICATION_SUFFIX, "");
        }
        final HttpAction action = client.redirect(playWebContext);
        return new PlayHttpActionAdapter().adapt(action.getCode(), playWebContext);
    }

    private String encodeRedirectUri(final String redirectUri) {
        try {
            return URLEncoder.encode(redirectUri, "UTF-8");
        } catch (UnsupportedEncodingException e) {
            throw new RuntimeException(String.format("Failed to encode redirect URI %s", redirectUri), e);
        }
    }

    private boolean tryLogin(String username, String password) {
        JsonNode invalidCredsJson = Json.newObject().put("message", "Invalid Credentials");
        boolean loginSucceeded = false;

        // First try jaas login, if enabled
        if (_jaasConfigs.isJAASEnabled()) {
            try {
                _logger.debug("Attempting jaas authentication");
                AuthenticationManager.authenticateJaasUser(username, password);
                loginSucceeded = true;
                _logger.debug("Jaas authentication successful");
            } catch (Exception e) {
                _logger.debug("Jaas authentication error", e);
            }
        }

        // If jaas login fails or is disabled, try native auth login
        if (_nativeAuthenticationConfigs.isNativeAuthenticationEnabled() && !loginSucceeded) {
            final Urn userUrn = new CorpuserUrn(username);
            final String userUrnString = userUrn.toString();
            loginSucceeded = loginSucceeded || _authClient.verifyNativeUserCredentials(userUrnString, password);
        }

        return loginSucceeded;
    }
}