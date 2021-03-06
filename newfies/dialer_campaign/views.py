#
# Newfies-Dialer License
# http://www.newfies-dialer.org
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (C) 2011-2013 Star2Billing S.L.
#
# The Initial Developer of the Original Code is
# Arezqui Belaid <info@star2billing.com>
#

from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, \
    permission_required
from django.http import HttpResponseRedirect, Http404
from django.shortcuts import render_to_response, get_object_or_404
from django.core.urlresolvers import reverse
from django.core.mail import mail_admins
from django.conf import settings
from django.template.context import RequestContext
from django.utils.translation import ugettext as _
from django.contrib.contenttypes.models import ContentType
from django.db.models import get_model
from dialer_contact.models import Phonebook
from dialer_campaign.models import Campaign
from dialer_campaign.forms import CampaignForm, DuplicateCampaignForm
from dialer_campaign.constants import CAMPAIGN_STATUS, CAMPAIGN_COLUMN_NAME
from dialer_campaign.function_def import check_dialer_setting, dialer_setting_limit, \
    user_dialer_setting, user_dialer_setting_msg
from dialer_campaign.tasks import collect_subscriber
from survey.models import Section, Branching, Survey_template
from user_profile.constants import NOTIFICATION_NAME
from frontend_notification.views import frontend_send_notification
from common.common_functions import current_view, get_pagination_vars
from dateutil.relativedelta import relativedelta
from datetime import datetime
import re


@login_required
def update_campaign_status_admin(request, pk, status):
    """Campaign Status (e.g. start|stop|pause|abort) can be changed from
    admin interface (via campaign list)"""
    obj_campaign = Campaign.objects.get(id=pk)
    obj_campaign.status = status
    obj_campaign.save()
    recipient = request.user
    frontend_send_notification(request, status, recipient)
    return HttpResponseRedirect(
        reverse("admin:dialer_campaign_campaign_changelist"))


@login_required
def update_campaign_status_cust(request, pk, status):
    """Campaign Status (e.g. start|stop|pause|abort) can be changed from
    customer interface (via dialer_campaign/campaign list)"""
    obj_campaign = Campaign.objects.get(id=pk)
    obj_campaign.status = status
    obj_campaign.save()

    pagination_path = '/campaign/'
    if request.session.get('pagination_path'):
        pagination_path = request.session.get('pagination_path')

    #Check if no phonebook attached
    if int(status) == CAMPAIGN_STATUS.START and obj_campaign.phonebook.all().count() == 0:
        request.session['error_msg'] = _('error : you have to assign a phonebook to your campaign before starting it')
    else:
        recipient = request.user
        frontend_send_notification(request, status, recipient)

        # Notify user while campaign Start
        if int(status) == CAMPAIGN_STATUS.START and not obj_campaign.has_been_started:
            request.session['info_msg'] = \
                _('the campaign global settings cannot be edited when the campaign is started')

            # change has_been_started flag
            obj_campaign.has_been_started = True
            obj_campaign.save()

            if obj_campaign.content_type.model == 'survey_template':
                # Copy survey
                survey_template = Survey_template.objects.get(user=request.user, pk=obj_campaign.object_id)
                survey_template.copy_survey_template(obj_campaign.id)
            collect_subscriber.delay(obj_campaign.id)

    return HttpResponseRedirect(pagination_path)


@login_required
def notify_admin(request):
    """Notify administrator regarding dialer setting configuration for
       system user via mail
    """
    # Get all the admin users - admin superuser
    all_admin_user = User.objects.filter(is_superuser=True)
    for user in all_admin_user:
        recipient = user
        if not request.session['has_notified']:
            frontend_send_notification(
                request, NOTIFICATION_NAME.dialer_setting_configuration, recipient)
            # Send mail to ADMINS
            subject = _('dialer setting configuration').title()
            message = _('Notification - User Dialer Setting. The user "%(user)s" - "%(user_id)s" is not properly configured to use the system, please configure their dialer settings.') %\
                {'user': request.user, 'user_id': request.user.id}
            # mail_admins() is a shortcut for sending an email to the site admins,
            # as defined in the ADMINS setting
            mail_admins(subject, message)
            request.session['has_notified'] = True

    return HttpResponseRedirect('/dashboard/')


def tpl_control_icon(icon):
    """
    function to produce control html icon
    """
    return 'style="text-decoration:none;background-image:url(%snewfies/icons/%s);"' % (settings.STATIC_URL, icon)


def get_url_campaign_status(id, status):
    """
    Helper to display campaign status button on the grid
    """
    #Store html for campaign control button
    control_play_style = tpl_control_icon('control_play.png')
    control_pause_style = tpl_control_icon('control_pause.png')
    control_abort_style = tpl_control_icon('control_abort.png')
    control_stop_style = tpl_control_icon('control_stop.png')

    #set different url for the campaign status
    url_cpg_status = 'update_campaign_status_cust/%s' % str(id)
    url_cpg_start = '%s/%s/' % (url_cpg_status, CAMPAIGN_STATUS.START)
    url_cpg_pause = '%s/%s/' % (url_cpg_status, CAMPAIGN_STATUS.PAUSE)
    url_cpg_abort = '%s/%s/' % (url_cpg_status, CAMPAIGN_STATUS.ABORT)
    url_cpg_stop = '%s/%s/' % (url_cpg_status, CAMPAIGN_STATUS.END)

    #according to the current status, disable link and change the button color
    if status == CAMPAIGN_STATUS.START:
        url_cpg_start = '#'
        control_play_style = tpl_control_icon('control_play_blue.png')
    elif status == CAMPAIGN_STATUS.PAUSE:
        url_cpg_pause = '#'
        control_pause_style = tpl_control_icon('control_pause_blue.png')
    elif status == CAMPAIGN_STATUS.ABORT:
        url_cpg_abort = '#'
        control_abort_style = tpl_control_icon('abort.png')
    elif status == CAMPAIGN_STATUS.END:
        url_cpg_stop = '#'
        control_stop_style = tpl_control_icon('control_stop_blue.png')

    #return all the html button for campaign status management
    return "<a href='%s' class='icon' title='%s' %s>&nbsp;</a> <a href='%s' class='icon' title='%s' %s>&nbsp;</a> <a href='%s' class='icon' title='%s' %s>&nbsp;</a> <a href='%s' class='icon' title='%s' %s>&nbsp;</a>" % \
        (url_cpg_start, _("start").capitalize(), control_play_style,
        url_cpg_pause, _("pause").capitalize(), control_pause_style,
        url_cpg_abort, _("abort").capitalize(), control_abort_style,
        url_cpg_stop, _("stop").capitalize(), control_stop_style)


def get_app_name(app_label, model_name, object_id):
    """To get app name from app_label, model_name & object_id"""
    try:
        return get_model(app_label, model_name).objects.get(pk=object_id)
    except:
        return '-'


def _return_link(app_name, obj_id):
    """
    Return link on campaign listing view
    """
    link = ''
    # Object view links
    if app_name == 'survey':
        link = '<a href="/survey_view/%s/" target="_blank" class="icon" title="%s" %s>&nbsp;</a>' % \
            (obj_id, _('survey').title(), tpl_control_icon('zoom.png'))

    # Object edit links
    if app_name == 'survey_template':
        link = '<a href="/survey/%s/" target="_blank" class="icon" title="%s" %s>&nbsp;</a>' %\
            (obj_id, _('edit survey').title(), tpl_control_icon('zoom.png'))

    return link


def get_campaign_survey_view(campaign_object):
    """display view button on campaign list"""
    link = ''
    if campaign_object.status and int(campaign_object.status) == CAMPAIGN_STATUS.START:
        if campaign_object.content_type.model == 'survey':
            link = _return_link('survey', campaign_object.object_id)

    if campaign_object.status and int(campaign_object.status) != CAMPAIGN_STATUS.START:

        if campaign_object.content_type.model == 'survey_template':
            link = _return_link('survey_template', campaign_object.object_id)

        if campaign_object.content_type.model == 'survey':
            link = _return_link('survey', campaign_object.object_id)

    return link


def make_duplicate_campaign(campaign_object_id):
    """Create link to make duplicate campaign"""
    link = '<a href="#campaign-duplicate"  url="/campaign_duplicate/%s/" class="campaign-duplicate icon" data-toggle="modal" data-controls-modal="campaign-duplicate" title="%s" %s>&nbsp;</a>'\
           % (campaign_object_id, _('duplicate this campaign').capitalize(),
              tpl_control_icon('layers.png'))
    return link


@permission_required('dialer_campaign.view_campaign', login_url='/')
@login_required
def campaign_list(request):
    """List all campaigns for the logged in user

    **Attributes**:

        * ``template`` - frontend/campaign/list.html

    **Logic Description**:

        * List all campaigns belonging to the logged in user
    """
    request.session['pagination_path'] = request.META['PATH_INFO'] + '?' + request.META['QUERY_STRING']
    sort_col_field_list = ['id', 'name', 'startingdate', 'status', 'totalcontact']
    default_sort_field = 'id'
    pagination_data =\
        get_pagination_vars(request, sort_col_field_list, default_sort_field)

    PAGE_SIZE = pagination_data['PAGE_SIZE']
    sort_order = pagination_data['sort_order']

    campaign_list = Campaign.objects.filter(user=request.user).order_by(sort_order)

    template = 'frontend/campaign/list.html'
    data = {
        'module': current_view(request),
        'campaign_list': campaign_list,
        'total_campaign': campaign_list.count(),
        'PAGE_SIZE': PAGE_SIZE,
        'CAMPAIGN_COLUMN_NAME': CAMPAIGN_COLUMN_NAME,
        'col_name_with_order': pagination_data['col_name_with_order'],
        'msg': request.session.get('msg'),
        'error_msg': request.session.get('error_msg'),
        'info_msg': request.session.get('info_msg'),
        'dialer_setting_msg': user_dialer_setting_msg(request.user),
    }
    request.session['msg'] = ''
    request.session['error_msg'] = ''
    request.session['info_msg'] = ''
    return render_to_response(template, data,
           context_instance=RequestContext(request))


def get_content_type(object_string):
    """
    Retrieve ContentType and Object ID from a string
    It is used by campaign_add & campaign_change

    #get_content_type("type:38-id:1")
    #{'object_type': <ContentType: Phonebook>, 'object_id': '1'}
    """
    contenttype = {}
    matches = re.match("type:(\d+)-id:(\d+)", object_string).groups()
    object_type_id = matches[0]  # get 45 from "type:45-id:38"
    contenttype['object_id'] = matches[1]  # get 38 from "type:45-id:38"
    contenttype['object_type'] = ContentType.objects.get(id=object_type_id)
    return contenttype


@permission_required('dialer_campaign.add_campaign', login_url='/')
@login_required
def campaign_add(request):
    """Add a new campaign for the logged in user

    **Attributes**:

        * ``form`` - CampaignForm
        * ``template`` - frontend/campaign/change.html

    **Logic Description**:

        * Before adding a campaign, check dialer setting limit if
          applicable to the user.
        * Add the new campaign which will belong to the logged in user
          via CampaignForm & get redirected to campaign list
    """
    # If dialer setting is not attached with user, redirect to campaign list
    if not user_dialer_setting(request.user):
        request.session['error_msg'] = \
            _("in order to add a campaign, you need to have your settings configured properly, please contact the admin.")
        return HttpResponseRedirect("/campaign/")

    # Check dialer setting limit
    if request.user and request.method != 'POST':
        # check Max Number of running campaign
        if check_dialer_setting(request, check_for="campaign"):
            msg = _("you have too many campaigns. Max allowed %(limit)s") \
                % {'limit': dialer_setting_limit(request, limit_for="campaign")}
            request.session['msg'] = msg

            # campaign limit reached
            frontend_send_notification(request, NOTIFICATION_NAME.campaign_limit_reached)
            return HttpResponseRedirect("/campaign/")

    form = CampaignForm(request.user)
    # Add campaign
    if request.method == 'POST':
        form = CampaignForm(request.user, request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            contenttype = get_content_type(form.cleaned_data['content_object'])
            obj.content_type = contenttype['object_type']
            obj.object_id = contenttype['object_id']
            obj.user = request.user
            obj.save()

            form.save_m2m()

            request.session["msg"] = _('"%(name)s" added.') %\
                {'name': request.POST['name']}
            return HttpResponseRedirect('/campaign/')

    template = 'frontend/campaign/change.html'
    data = {
        'module': current_view(request),
        'form': form,
        'action': 'add',
        'AMD': settings.AMD,
    }
    return render_to_response(template, data,
           context_instance=RequestContext(request))


@permission_required('dialer_campaign.delete_campaign', login_url='/')
@login_required
def campaign_del(request, object_id):
    """Delete campaign for the logged in user

    **Attributes**:

        * ``object_id`` - Selected campaign object
        * ``object_list`` - Selected campaign objects

    **Logic Description**:

        * Delete the selected campaign from the campaign list
    """
    if int(object_id) != 0:
        # When object_id is not 0
        campaign = get_object_or_404(Campaign, pk=object_id, user=request.user)
        request.session["msg"] = _('"%(name)s" is deleted.')\
            % {'name': campaign.name}
        campaign.delete()
    else:
        # When object_id is 0 (Multiple records delete)
        values = request.POST.getlist('select')
        values = ", ".join(["%s" % el for el in values])
        try:
            campaign_list = Campaign.objects\
                .filter(user=request.user)\
                .extra(where=['id IN (%s)' % values])
            if campaign_list:
                request.session["msg"] = _('%(count)s campaign(s) are deleted.')\
                    % {'count': campaign_list.count()}
                campaign_list.delete()
        except:
            raise Http404

    return HttpResponseRedirect('/campaign/')


@permission_required('dialer_campaign.change_campaign', login_url='/')
@login_required
def campaign_change(request, object_id):
    """Update/Delete campaign for the logged in user

    **Attributes**:

        * ``object_id`` - Selected campaign object
        * ``form`` - CampaignForm
        * ``template`` - frontend/campaign/change.html

    **Logic Description**:

        * Update/delete selected campaign from the campaign list
          via CampaignForm & get redirected to the campaign list
    """
    # If dialer setting is not attached with user, redirect to campaign list
    if not user_dialer_setting(request.user):
        return HttpResponseRedirect("/campaign/")

    campaign = get_object_or_404(Campaign, pk=object_id, user=request.user)

    content_object = "type:%s-id:%s" % \
        (campaign.content_type_id, campaign.object_id)
    form = CampaignForm(request.user,
                        instance=campaign,
                        initial={'content_object': content_object})

    if campaign.status == CAMPAIGN_STATUS.START:
        request.session['info_msg'] =\
            _('the campaign is started, you can only edit Dialer settings and Campaign schedule')

    if request.method == 'POST':
        # Delete campaign
        if request.POST.get('delete'):            
            return HttpResponseRedirect('/campaign/del/%s/' % object_id)
        else:
            # Update campaign
            form = CampaignForm(request.user, request.POST, instance=campaign)

            if form.is_valid():
                form.save()
                obj = form.save(commit=False)

                selected_content_object = form.cleaned_data['content_object']
                if not selected_content_object:
                    selected_content_object = form.cleaned_data['selected_content_object']
                # while campaign status is running
                if campaign.status == CAMPAIGN_STATUS.START:
                    if request.POST.get('selected_phonebook'):
                        selected_phonebook = str(request.POST
                            .get('selected_phonebook')).split(',')
                        obj.phonebook = Phonebook.objects\
                            .filter(id__in=selected_phonebook)

                contenttype = get_content_type(selected_content_object)
                obj.content_type = contenttype['object_type']
                obj.object_id = contenttype['object_id']
                obj.save()

                request.session["msg"] = _('"%(name)s" is updated.') \
                    % {'name': request.POST['name']}
                request.session['error_msg'] = ''
                return HttpResponseRedirect('/campaign/')

    template = 'frontend/campaign/change.html'
    data = {
        'module': current_view(request),
        'form': form,
        'action': 'update',
        'error_msg': request.session.get('error_msg'),
        'info_msg': request.session.get('info_msg'),
        'AMD': settings.AMD,
    }
    request.session['error_msg'] = ''
    request.session['info_msg'] = ''
    return render_to_response(template, data,
           context_instance=RequestContext(request))


def make_duplicate_survey(campaign_obj, new_campaign):
    """Make duplicate survey with section & branching
       & return new survey object id
    """
    survey_obj = campaign_obj.content_type.model_class().objects.get(pk=campaign_obj.object_id)
    original_survey_id = survey_obj.id

    # make clone of survey
    survey_obj.pk = None
    survey_obj.campaign = new_campaign
    survey_obj.save()

    old_new_section_dict = {}
    section_objs = Section.objects.filter(survey_id=original_survey_id).order_by('order')
    for section_obj in section_objs:
        old_section_id = section_obj.id

        # make clone of section
        section_obj.pk = None
        section_obj.survey = survey_obj
        section_obj.save()

        old_new_section_dict[old_section_id] = section_obj.id

    for old_section_id, new_section_id in old_new_section_dict.iteritems():
        branching_objs = Branching.objects.filter(section_id=old_section_id)

        for branching_obj in branching_objs:
            new_goto_id = None
            if branching_obj.goto_id is not None:
                new_goto_id = old_new_section_dict[branching_obj.goto_id]

            branching_obj.pk = None
            branching_obj.section_id = new_section_id
            branching_obj.goto_id = new_goto_id
            branching_obj.save()

    return survey_obj.id


@login_required
def campaign_duplicate(request, id):
    """
    Duplicate campaign via DuplicateCampaignForm

    **Attributes**:

        * ``id`` - Selected campaign object
        * ``form`` - DuplicateCampaignForm
        * ``template`` - frontend/campaign/campaign_duplicate.html
    """
    form = DuplicateCampaignForm(request.user)
    request.session['error_msg'] = ''
    if request.method == 'POST':
        form = DuplicateCampaignForm(request.user, request.POST)
        if form.is_valid():

            original_camp = campaign_obj = Campaign.objects.get(pk=id)
            #Make duplicate survey
            new_survey_id = campaign_obj.object_id

            campaign_obj.pk = None
            campaign_obj.campaign_code = request.POST.get('campaign_code')
            campaign_obj.name = request.POST.get('name')
            campaign_obj.status = CAMPAIGN_STATUS.PAUSE
            campaign_obj.totalcontact = 0
            campaign_obj.completed = 0
            campaign_obj.startingdate = datetime.now()
            campaign_obj.expirationdate = datetime.now() + relativedelta(days=+1)
            campaign_obj.imported_phonebook = ''
            campaign_obj.has_been_started = False
            campaign_obj.has_been_duplicated = True
            campaign_obj.save()

            if campaign_obj.content_type.model == 'survey':
                new_survey_id = make_duplicate_survey(original_camp, campaign_obj)

            campaign_obj.object_id = new_survey_id
            campaign_obj.save()

            # Many to many field
            for pb in request.POST.getlist('phonebook'):
                campaign_obj.phonebook.add(pb)

            return HttpResponseRedirect('/campaign/')
        else:
            request.session['error_msg'] = True
    else:
        request.session['error_msg'] = ''

    template = 'frontend/campaign/campaign_duplicate.html'
    data = {
        'module': current_view(request),
        'campaign_id': id,
        'form': form,
        'err_msg': request.session.get('error_msg'),
    }
    request.session['error_msg'] = ''
    return render_to_response(template, data,
        context_instance=RequestContext(request))
