""" Admin endpoints """
import time
import re
import datetime
import random
from io import BytesIO

from peewee import fn, JOIN
import pyotp
import qrcode
from flask import (
    Blueprint,
    abort,
    redirect,
    url_for,
    session,
    render_template,
    request,
    send_file,
)
from flask_login import login_required, current_user
from flask_babel import _
from .. import misc
from ..config import config
from ..forms import (
    TOTPForm,
    LogOutForm,
    UseInviteCodeForm,
    AssignUserBadgeForm,
    EditModForm,
    BanDomainForm,
    WikiForm,
    CreateInviteCodeForm,
    UpdateInviteCodeForm,
    EditBadgeForm,
    NewBadgeForm,
    SetSubOfTheDayForm,
    ChangeConfigSettingForm,
)
from ..models import (
    UserMetadata,
    User,
    Sub,
    SubPost,
    SubPostComment,
    SubPostCommentVote,
    SubPostVote,
    SiteMetadata,
)
from ..models import UserUploads, InviteCode, Wiki
from ..misc import engine, getReports
from ..badges import badges

bp = Blueprint("admin", __name__)


@bp.route("/admin/auth", methods=["GET", "POST"])
@login_required
def auth():
    if not current_user.can_admin:
        abort(404)
    form = TOTPForm()
    try:
        user_secret = UserMetadata.get(
            (UserMetadata.uid == current_user.uid) & (UserMetadata.key == "totp_secret")
        )
    except UserMetadata.DoesNotExist:
        user_secret = UserMetadata.create(
            uid=current_user.uid, key="totp_secret", value=pyotp.random_base32(64)
        )

    template = "admin/totp_setup.html"
    try:
        UserMetadata.get(
            (UserMetadata.uid == current_user.uid)
            & (UserMetadata.key == "totp_setup_finished")
        )
        template = "admin/totp.html"
    except UserMetadata.DoesNotExist:
        pass

    if form.validate_on_submit():
        totp = pyotp.TOTP(user_secret.value)
        if totp.verify(form.totp.data):
            session["apriv"] = time.time()
            UserMetadata.create(
                uid=current_user.uid, key="totp_setup_finished", value="1"
            )
            return redirect(url_for("admin.index"))
        else:
            return engine.get_template(template).render(
                {"authform": form, "error": _("Invalid or expired token.")}
            )

    return engine.get_template(template).render({"authform": form, "error": None})


@bp.route("/totp_image", methods=["GET"])
@login_required
def get_totp_image():
    """
    Returns a QR code used to set up TOTP
    """
    if not current_user.can_admin:
        abort(404)

    try:
        user_secret = UserMetadata.get(
            (UserMetadata.uid == current_user.uid) & (UserMetadata.key == "totp_secret")
        )
    except UserMetadata.DoesNotExist:
        user_secret = UserMetadata.create(
            uid=current_user.uid, key="totp_secret", value=pyotp.random_base32(64)
        )

    try:
        UserMetadata.get(
            (UserMetadata.uid == current_user.uid)
            & (UserMetadata.key == "totp_setup_finished")
        )
        # TOTP setup already finished, we won't reveal the secret anymore
        return abort(403)
    except UserMetadata.DoesNotExist:
        pass

    uri = pyotp.totp.TOTP(user_secret.value).provisioning_uri(
        name=current_user.name, issuer_name=config.site.name
    )
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(uri)

    img = qr.make_image(fill_color="black", back_color="white")

    img_io = BytesIO()
    img.save(img_io, "PNG")
    img_io.seek(0)
    return send_file(img_io, mimetype="image/png")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    if not current_user.can_admin:
        abort(404)
    form = LogOutForm()
    if form.validate():
        del session["apriv"]
    return redirect(url_for("admin.index"))


@bp.route("/")
@login_required
def index():
    """ WIP: View users. assign badges, etc """
    if not current_user.can_admin:
        abort(404)

    if not current_user.admin:
        return redirect(url_for("admin.auth"))

    users = User.select().count()
    subs = Sub.select().count()
    posts = SubPost.select().count()
    comms = SubPostComment.select().count()
    ups = SubPostVote.select().where(SubPostVote.positive == 1).count()
    downs = SubPostVote.select().where(SubPostVote.positive == 0).count()
    ups += SubPostCommentVote.select().where(SubPostCommentVote.positive == 1).count()
    downs += SubPostCommentVote.select().where(SubPostCommentVote.positive == 0).count()

    invite = UseInviteCodeForm()
    invite.minlevel.data = config.site.invite_level
    invite.maxcodes.data = config.site.invite_max

    subOfTheDay = SetSubOfTheDayForm()

    return render_template(
        "admin/admin.html",
        subs=subs,
        posts=posts,
        ups=ups,
        downs=downs,
        users=users,
        comms=comms,
        subOfTheDay=subOfTheDay,
        useinvitecodeform=invite,
    )


@bp.route("/users", defaults={"page": 1})
@bp.route("/users/<int:page>")
@login_required
def users(page):
    """ WIP: View users. """
    if not current_user.is_admin():
        abort(404)

    postcount = (
        SubPost.select(SubPost.uid, fn.Count(SubPost.pid).alias("post_count"))
        .group_by(SubPost.uid)
        .alias("post_count")
    )
    commcount = (
        SubPostComment.select(
            SubPostComment.uid, fn.Count(SubPostComment.cid).alias("comment_count")
        )
        .group_by(SubPostComment.uid)
        .alias("j2")
    )

    users = User.select(
        User.name,
        User.status,
        User.uid,
        User.joindate,
        postcount.c.post_count.alias("post_count"),
        commcount.c.comment_count,
    )
    users = users.join(postcount, JOIN.LEFT_OUTER, on=User.uid == postcount.c.uid)
    users = users.join(commcount, JOIN.LEFT_OUTER, on=User.uid == commcount.c.uid)
    users = users.order_by(User.joindate.desc()).paginate(page, 50).dicts()
    return render_template(
        "admin/users.html", users=users, page=page, admin_route="admin.users"
    )


@bp.route("/userbadges")
@login_required
def userbadges():
    """ WIP: Assign user badges. """
    if not current_user.is_admin():
        abort(404)

    form = AssignUserBadgeForm()
    form.badge.choices = [(badge.bid, badge.name) for badge in badges]
    ct = UserMetadata.select().where(UserMetadata.key == "badge").count()
    return render_template(
        "admin/userbadges.html",
        badges=badges,
        assignuserbadgeform=form,
        ct=ct,
        admin_route="admin.userbadges",
    )


@bp.route("/userbadges/new", methods=["GET", "POST"])
@login_required
def newbadge():
    """Edit badge information."""
    if not current_user.is_admin():
        abort(404)

    form = NewBadgeForm()
    form.trigger.choices = [(None, "No Trigger")] + [
        (trigger, trigger) for trigger in badges.triggers()
    ]
    if form.validate_on_submit():
        icon = request.files.get(form.icon.name)
        badges.new_badge(
            name=form.name.data,
            alt=form.alt.data,
            score=form.score.data,
            trigger=form.trigger.data,
            rank=form.rank.data,
            icon=icon,
        )
        return redirect(url_for("admin.userbadges"))
    return render_template("admin/editbadge.html", form=form, badge=None, new=True)


@bp.route("/userbadges/edit/<int:badge>", methods=["GET", "POST"])
@login_required
def editbadge(badge):
    """Edit badge information."""
    if not current_user.is_admin():
        abort(404)

    badge = badges[badge]

    form = EditBadgeForm()
    form.trigger.choices = [(None, "No Trigger")] + [
        (trigger, trigger) for trigger in badges.triggers()
    ]
    if form.validate_on_submit():
        icon = request.files.get(form.icon.name)
        badges.update_badge(
            bid=badge.bid,
            name=form.name.data,
            alt=form.alt.data,
            score=form.score.data,
            trigger=form.trigger.data,
            rank=form.rank.data,
            icon=icon,
        )
        return redirect(url_for("admin.userbadges"))
    form.name.data = badge.name
    form.alt.data = badge.alt
    form.score.data = badge.score
    form.trigger.data = badge.trigger
    form.rank.data = badge.rank
    return render_template("admin/editbadge.html", form=form, badge=badge, new=False)


@bp.route("/userbadges/delete/<int:badge>", methods=["POST"])
@login_required
def deletebadge(badge):
    """Edit badge information."""
    if not current_user.is_admin():
        abort(404)

    badges.delete_badge(badge)
    return redirect(url_for("admin.userbadges"))


@bp.route("/invitecodes", defaults={"page": 1}, methods=["GET", "POST"])
@bp.route("/invitecodes/<int:page>", methods=["GET", "POST"])
@login_required
def invitecodes(page):
    """
    View and configure Invite Codes
    """

    def map_style(code):
        if code["uses"] >= code["max_uses"]:
            return "expired"
        elif (
            code["expires"] is not None and code["expires"] < datetime.datetime.utcnow()
        ):
            return "expired"
        else:
            return ""

    if not current_user.is_admin():
        abort(404)

    invite_codes = (
        InviteCode.select(
            InviteCode.id,
            InviteCode.code,
            User.name.alias("created_by"),
            InviteCode.created,
            InviteCode.expires,
            InviteCode.uses,
            InviteCode.max_uses,
        )
        .join(User)
        .order_by(InviteCode.uses.desc(), InviteCode.created.desc())
        .paginate(page, 50)
        .dicts()
    )

    code_users = (
        UserMetadata.select(
            User.name.alias("used_by"), User.status, UserMetadata.value.alias("code")
        )
        .where(
            (UserMetadata.key == "invitecode")
            & (UserMetadata.value << set([x["code"] for x in invite_codes]))
        )
        .join(User)
        .dicts()
    )

    used_by = {}
    for user in code_users:
        if not user["code"] in used_by:
            used_by[user["code"]] = []
        used_by[user["code"]].append((user["used_by"], user["status"]))

    update_form = UpdateInviteCodeForm()

    for code in invite_codes:
        code["style"] = map_style(code)
        code["used_by"] = used_by.get(code["code"], [])
        code["created"] = code["created"].strftime("%Y-%m-%dT%H:%M:%SZ")
        if code["expires"] is not None:
            code["expires"] = code["expires"].strftime("%Y-%m-%dT%H:%M:%SZ")

    invite_form = UseInviteCodeForm()
    invite_form.maxcodes.data = config.site.invite_max
    invite_form.minlevel.data = config.site.invite_level

    form = CreateInviteCodeForm()

    if form.validate_on_submit():
        if form.code.data:
            # The admin has typed in a particular code.
            code = form.code.data
        else:
            code = "".join(
                random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(32)
            )

        user_id = current_user.uid
        expires = form.expires.data or None
        max_uses = form.uses.data

        InviteCode.create(
            user=user_id,
            code=code,
            max_uses=max_uses,
            expires=expires,
        )
        return redirect(url_for("admin.invitecodes", page=page))

    if update_form.validate_on_submit():
        if update_form.etype.data == "at" and update_form.expires.data is None:
            update_form.etype.data = "never"
        ids = [
            invite_codes[int(code.id.split("-")[1])]["id"] for code in update_form.codes
        ]
        if ids:
            if update_form.etype.data == "now":
                expires = datetime.datetime.utcnow()
            elif update_form.etype.data == "never" or update_form.expires.data is None:
                expires = None
            else:
                expires = form.expires.data
            InviteCode.update(expires=expires).where(InviteCode.id << ids).execute()
        return redirect(url_for("admin.invitecodes", page=page))

    return render_template(
        "admin/invitecodes.html",
        useinvitecodeform=invite_form,
        invite_codes=invite_codes,
        page=page,
        error=misc.get_errors(form, True),
        form=form,
        update_form=update_form,
    )


@bp.route("/admins")
@login_required
def view():
    """ WIP: View admins. """
    if not current_user.is_admin():
        abort(404)
    admins = UserMetadata.select().where(UserMetadata.key == "admin")

    postcount = (
        SubPost.select(SubPost.uid, fn.Count(SubPost.pid).alias("post_count"))
        .group_by(SubPost.uid)
        .alias("post_count")
    )
    commcount = (
        SubPostComment.select(
            SubPostComment.uid, fn.Count(SubPostComment.cid).alias("comment_count")
        )
        .group_by(SubPostComment.uid)
        .alias("j2")
    )

    users = User.select(
        User.name,
        User.status,
        User.uid,
        User.joindate,
        postcount.c.post_count.alias("post_count"),
        commcount.c.comment_count,
    )
    users = users.join(postcount, JOIN.LEFT_OUTER, on=User.uid == postcount.c.uid)
    users = users.join(commcount, JOIN.LEFT_OUTER, on=User.uid == commcount.c.uid)
    users = (
        users.where(User.uid << [x.uid for x in admins])
        .order_by(User.joindate.asc())
        .dicts()
    )

    return render_template("admin/users.html", users=users, admin_route="admin.view")


@bp.route("/usersearch/<term>")
@login_required
def users_search(term):
    """ WIP: Search users. """
    if not current_user.is_admin():
        abort(404)
    term = re.sub(r"[^A-Za-z0-9.\-_]+", "", term)

    postcount = (
        SubPost.select(SubPost.uid, fn.Count(SubPost.pid).alias("post_count"))
        .group_by(SubPost.uid)
        .alias("post_count")
    )
    commcount = (
        SubPostComment.select(
            SubPostComment.uid, fn.Count(SubPostComment.cid).alias("comment_count")
        )
        .group_by(SubPostComment.uid)
        .alias("j2")
    )

    users = User.select(
        User.name,
        User.status,
        User.uid,
        User.joindate,
        postcount.c.post_count,
        commcount.c.comment_count,
    )
    users = users.join(postcount, JOIN.LEFT_OUTER, on=User.uid == postcount.c.uid)
    users = users.join(commcount, JOIN.LEFT_OUTER, on=User.uid == commcount.c.uid)
    users = users.where(User.name.contains(term)).order_by(User.joindate.desc()).dicts()

    return render_template(
        "admin/users.html", users=users, term=term, admin_route="admin.users_search"
    )


@bp.route("/subs", defaults={"page": 1})
@bp.route("/subs/<int:page>")
@login_required
def subs(page):
    """ WIP: View subs. Assign new owners """
    if not current_user.is_admin():
        abort(404)
    subs = Sub.select().paginate(page, 50)
    return render_template(
        "admin/subs.html",
        subs=subs,
        page=page,
        admin_route="admin.subs",
        editmodform=EditModForm(),
    )


@bp.route("/subsearch/<term>")
@login_required
def subs_search(term):
    """ WIP: Search for a sub. """
    if not current_user.is_admin():
        abort(404)
    term = re.sub(r"[^A-Za-z0-9.\-_]+", "", term)
    subs = Sub.select().where(Sub.name.contains(term))
    return render_template(
        "admin/subs.html",
        subs=subs,
        term=term,
        admin_route="admin.subs_search",
        editmodform=EditModForm(),
    )


@bp.route("/posts/all/", defaults={"page": 1})
@bp.route("/posts/all/<int:page>")
@login_required
def posts(page):
    """ WIP: View posts. """
    if not current_user.is_admin():
        abort(404)
    posts = (
        misc.getPostList(
            misc.postListQueryBase(include_deleted_posts=True), "new", page
        )
        .paginate(page, 50)
        .dicts()
    )
    return render_template(
        "admin/posts.html", page=page, admin_route="admin.posts", posts=posts
    )


@bp.route("/postvoting/<term>", defaults={"page": 1})
@bp.route("/postvoting/<term>/<int:page>")
@login_required
def post_voting(page, term):
    """ WIP: View post voting habits """
    if not current_user.is_admin():
        abort(404)
    try:
        user = User.get(fn.Lower(User.name) == term.lower())
        msg = []
        votes = SubPostVote.select(
            SubPostVote.positive,
            SubPostVote.pid,
            User.name,
            SubPostVote.datetime,
            SubPostVote.pid,
        )
        votes = votes.join(SubPost, JOIN.LEFT_OUTER, on=SubPost.pid == SubPostVote.pid)
        votes = votes.switch(SubPost).join(
            User, JOIN.LEFT_OUTER, on=SubPost.uid == User.uid
        )
        votes = votes.where(SubPostVote.uid == user.uid).dicts()
    except User.DoesNotExist:
        votes = []
        msg = "user not found"

    return render_template(
        "admin/postvoting.html",
        page=page,
        msg=msg,
        admin_route="admin.post_voting",
        votes=votes,
        term=term,
    )


@bp.route("/commentvoting/<term>", defaults={"page": 1})
@bp.route("/commentvoting/<term>/<int:page>")
@login_required
def comment_voting(page, term):
    """ WIP: View comment voting habits """
    if not current_user.is_admin():
        abort(404)
    try:
        user = User.get(fn.Lower(User.name) == term.lower())
        msg = []
        votes = SubPostCommentVote.select(
            SubPostCommentVote.positive,
            SubPostCommentVote.cid,
            SubPostComment.uid,
            User.name,
            SubPostCommentVote.datetime,
            SubPost.pid,
            Sub.name.alias("sub"),
        )
        votes = (
            votes.join(
                SubPostComment,
                JOIN.LEFT_OUTER,
                on=SubPostComment.cid == SubPostCommentVote.cid,
            )
            .join(SubPost)
            .join(Sub)
        )
        votes = votes.switch(SubPostComment).join(
            User, JOIN.LEFT_OUTER, on=SubPostComment.uid == User.uid
        )
        votes = votes.where(SubPostCommentVote.uid == user.uid).dicts()
    except User.DoesNotExist:
        votes = []
        msg = "user not found"

    return render_template(
        "admin/commentvoting.html",
        page=page,
        msg=msg,
        admin_route="admin.comment_voting",
        votes=votes,
        term=term,
    )


@bp.route("/post/search/<term>")
@login_required
def post_search(term):
    """ WIP: Post search result. """
    if not current_user.is_admin():
        abort(404)
    term = re.sub(r"[^A-Za-z0-9.\-_]+", "", term)
    try:
        post = SubPost.get(SubPost.pid == term)
    except SubPost.DoesNotExist:
        return abort(404)

    votes = (
        SubPostVote.select(SubPostVote.positive, SubPostVote.datetime, User.name)
        .join(User)
        .where(SubPostVote.pid == post.pid)
        .dicts()
    )
    upcount = post.votes.where(SubPostVote.positive == "1").count()
    downcount = post.votes.where(SubPostVote.positive == "0").count()

    pcount = post.uid.posts.count()
    ccount = post.uid.comments.count()
    comms = (
        SubPostComment.select(
            SubPostComment.score, SubPostComment.content, SubPostComment.cid, User.name
        )
        .join(User)
        .where(SubPostComment.pid == post.pid)
        .dicts()
    )

    return render_template(
        "admin/post.html",
        sub=post.sid,
        post=post,
        votes=votes,
        ccount=ccount,
        pcount=pcount,
        upcount=upcount,
        downcount=downcount,
        comms=comms,
        user=post.uid,
    )


@bp.route("/domains/<domain_type>", defaults={"page": 1})
@bp.route("/domains/<domain_type>/<int:page>")
@login_required
def domains(domain_type, page):
    """ WIP: View Banned Domains """
    if not current_user.is_admin():
        abort(404)
    if domain_type == "email":
        key = "banned_email_domain"
        title = _("Banned Email Domains")
    elif domain_type == "link":
        key = "banned_domain"
        title = _("Banned Domains")
    else:
        return abort(404)
    domains = (
        SiteMetadata.select()
        .where(SiteMetadata.key == key)
        .order_by(SiteMetadata.value)
    )
    return render_template(
        "admin/domains.html",
        domains=domains,
        title=title,
        domain_type=domain_type,
        page=page,
        bandomainform=BanDomainForm(),
    )


@bp.route("/uploads", defaults={"page": 1})
@bp.route("/uploads/<int:page>")
@login_required
def user_uploads(page):
    """ View user uploads """
    if not current_user.is_admin():
        abort(404)
    uploads = UserUploads.select().order_by(UserUploads.pid.desc()).paginate(page, 30)
    users = (
        User.select(User.name).join(UserMetadata).where(UserMetadata.key == "canupload")
    )
    return render_template(
        "admin/uploads.html", page=page, uploads=uploads, users=users
    )


@bp.route("/reports", defaults={"page": 1})
@bp.route("/reports/<int:page>")
@login_required
def reports(page):
    if not current_user.is_admin():
        abort(404)

    reports = getReports("admin", "all", page)

    return engine.get_template("admin/reports.html").render(
        {
            "reports": reports,
            "page": page,
            "sub": False,
            "subInfo": False,
            "subMods": False,
        }
    )


@bp.route("/configuration")
@login_required
def configure():
    if not current_user.is_admin():
        abort(404)

    form = ChangeConfigSettingForm()

    config_data = sorted(config.get_mutable_items(), key=(lambda x: x["name"]))
    return engine.get_template("admin/configuration.html").render(
        {"form": form, "config_data": config_data}
    )


@bp.route("/wiki", defaults={"page": 1})
@bp.route("/wiki/<int:page>")
@login_required
def wiki(page):
    if not current_user.is_admin():
        abort(404)

    pages = Wiki.select().where(Wiki.is_global)

    return engine.get_template("admin/wiki.html").render({"wikis": pages, "page": page})


@bp.route("/wiki/create", methods=["GET", "POST"])
@login_required
def create_wiki():
    if not current_user.is_admin():
        abort(404)

    form = WikiForm()

    if form.validate_on_submit():
        Wiki.create(
            slug=form.slug.data,
            title=form.title.data,
            content=form.content.data,
            is_global=True,
            sub=None,
        )
        return redirect(url_for("admin.wiki"))
    return engine.get_template("admin/createwiki.html").render(
        {"form": form, "error": misc.get_errors(form, True)}
    )


@bp.route("/wiki/edit/<slug>", methods=["GET"])
@login_required
def edit_wiki(slug):
    if not current_user.is_admin():
        abort(404)

    form = WikiForm()
    try:
        wiki_page = Wiki.select().where(Wiki.slug == slug).where(Wiki.is_global).get()
    except Wiki.DoesNotExist:
        return abort(404)

    form.slug.data = wiki_page.slug
    form.content.data = wiki_page.content
    form.title.data = wiki_page.title

    return engine.get_template("admin/createwiki.html").render(
        {"form": form, "error": misc.get_errors(form, True)}
    )


@bp.route("/wiki/edit/<slug>", methods=["POST"])
@login_required
def edit_wiki_save(slug):
    if not current_user.is_admin():
        abort(404)

    form = WikiForm()
    try:
        wiki_page = Wiki.select().where(Wiki.slug == slug).where(Wiki.is_global).get()
    except Wiki.DoesNotExist:
        return abort(404)

    if form.validate_on_submit():
        wiki_page.slug = form.slug.data
        wiki_page.title = form.title.data
        wiki_page.content = form.content.data
        wiki_page.updated = datetime.datetime.utcnow()
        wiki_page.save()
        return redirect(url_for("admin.wiki"))

    return engine.get_template("admin/createwiki.html").render(
        {"form": form, "error": misc.get_errors(form, True)}
    )


@bp.route("/wiki/delete/<slug>", methods=["GET"])
@login_required
def delete_wiki(slug):
    if not current_user.is_admin():
        abort(404)

    # XXX: This could be an ajax call
    try:
        wiki_page = Wiki.select().where(Wiki.slug == slug).where(Wiki.is_global).get()
    except Wiki.DoesNotExist:
        return abort(404)

    wiki_page.delete_instance()
    return redirect(url_for("admin.wiki"))
