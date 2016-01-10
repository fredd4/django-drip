
import uuid
from datetime import datetime, timedelta

from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings

# just using this to parse, but totally insane package naming...
# https://bitbucket.org/schinckel/django-timedelta-field/
import timedelta as djangotimedelta


def unique_code():
    return str(uuid.uuid4())


class Drip(models.Model):
    date = models.DateTimeField(auto_now_add=True)
    lastchanged = models.DateTimeField(auto_now=True)

    name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name='Drip Name',
        help_text='A unique name for this drip.')

    enabled = models.BooleanField(default=False)
    marketing = models.BooleanField(default=True,
        help_text='If true, users who have unsubscribed will not get this ' \
                  'email. An unsubscribe link will be added automatically to ' \
                  'the email footer.')

    from_email = models.EmailField(null=True, blank=True,
        help_text='Set a custom from email.')
    from_email_name = models.CharField(max_length=150, null=True, blank=True,
        help_text="Set a name for a custom from email.")
    reply_to = models.EmailField(null=True, blank=True,
        help_text='Set a custom reply-to email.')
    subject_template = models.TextField(null=True, blank=True)
    body_html_template = models.TextField(null=True, blank=True,
        help_text='You will have settings and user in the context.')
    message_class = models.CharField(max_length=120, blank=True, default='default')

    @property
    def drip(self):
        from drip.drips import DripBase

        drip = DripBase(drip_model=self,
                        name=self.name,
                        from_email=self.from_email if self.from_email else None,
                        from_email_name=self.from_email_name if self.from_email_name else None,
                        reply_to=self.reply_to if self.reply_to else None,
                        subject_template=self.subject_template if self.subject_template else None,
                        body_template=self.body_html_template if self.body_html_template else None)
        return drip

    def __unicode__(self):
        return self.name


class SentDrip(models.Model):
    """
    Keeps a record of all sent drips.
    """
    date = models.DateTimeField(auto_now_add=True)

    drip = models.ForeignKey('drip.Drip', related_name='sent_drips')
    user = models.ForeignKey(getattr(settings, 'AUTH_USER_MODEL', 'auth.User'), related_name='sent_drips')

    subject = models.TextField()
    body = models.TextField()
    from_email = models.EmailField(null=True, default=None)
    from_email_name = models.CharField(max_length=150, null=True, default=None)
    reply_to = models.EmailField(null=True, default=None)



METHOD_TYPES = (
    ('filter', 'Filter'),
    ('exclude', 'Exclude'),
)

LOOKUP_TYPES = (
    ('exact', 'exactly'),
    ('iexact', 'exactly (case insensitive)'),
    ('contains', 'contains'),
    ('icontains', 'contains (case insensitive)'),
    ('regex', 'regex'),
    ('iregex', 'contains (case insensitive)'),
    ('gt', 'greater than'),
    ('gte', 'greater than or equal to'),
    ('lt', 'less than'),
    ('lte', 'less than or equal to'),
    ('startswith', 'starts with'),
    ('endswith', 'starts with'),
    ('istartswith', 'ends with (case insensitive)'),
    ('iendswith', 'ends with (case insensitive)'),
)

class QuerySetRule(models.Model):
    date = models.DateTimeField(auto_now_add=True)
    lastchanged = models.DateTimeField(auto_now=True)

    drip = models.ForeignKey(Drip, related_name='queryset_rules')

    method_type = models.CharField(max_length=12, default='filter', choices=METHOD_TYPES)
    field_name = models.CharField(max_length=128, verbose_name='Field name of User')
    lookup_type = models.CharField(max_length=12, default='exact', choices=LOOKUP_TYPES)

    field_value = models.CharField(max_length=255,
        help_text=('Can be anything from a number, to a string. Or, do ' +
                   '`now-7 days` or `today+3 days` for fancy timedelta.'))

    def clean(self):
        try:
            self.apply(User.objects.all())
        except Exception as e:
            raise ValidationError(
                '%s raised trying to apply rule: %s' % (type(e).__name__, e))

    @property
    def annotated_field_name(self):
        field_name = self.field_name
        if field_name.endswith('__count'):
            agg, _, _ = field_name.rpartition('__')
            field_name = 'num_%s' % agg.replace('__', '_')

        return field_name

    def apply_any_annotation(self, qs):
        if self.field_name.endswith('__count'):
            field_name = self.annotated_field_name
            agg, _, _ = self.field_name.rpartition('__')
            qs = qs.annotate(**{field_name: models.Count(agg, distinct=True)})
        return qs

    def filter_kwargs(self, qs, now=datetime.now):
        # Support Count() as m2m__count
        field_name = self.annotated_field_name
        field_name = '__'.join([field_name, self.lookup_type])
        field_value = self.field_value

        # set time deltas and dates
        if self.field_value.startswith('now-'):
            field_value = self.field_value.replace('now-', '')
            field_value = now() - djangotimedelta.parse(field_value)
        elif self.field_value.startswith('now+'):
            field_value = self.field_value.replace('now+', '')
            field_value = now() + djangotimedelta.parse(field_value)
        elif self.field_value.startswith('today-'):
            field_value = self.field_value.replace('today-', '')
            field_value = now().date() - djangotimedelta.parse(field_value)
        elif self.field_value.startswith('today+'):
            field_value = self.field_value.replace('today+', '')
            field_value = now().date() + djangotimedelta.parse(field_value)

        # F expressions
        if self.field_value.startswith('F_'):
            field_value = self.field_value.replace('F_', '')
            field_value = models.F(field_value)

        # set booleans
        if self.field_value == 'True':
            field_value = True
        if self.field_value == 'False':
            field_value = False

        kwargs = {field_name: field_value}

        return kwargs

    def apply(self, qs, now=datetime.now):

        kwargs = self.filter_kwargs(qs, now)
        qs = self.apply_any_annotation(qs)

        if self.method_type == 'filter':
            return qs.filter(**kwargs)
        elif self.method_type == 'exclude':
            return qs.exclude(**kwargs)

        # catch as default
        return qs.filter(**kwargs)


class Subscription(models.Model):
    """ Indicates whether a user is unsubscribed from marketing emails. """
    user = models.OneToOneField(getattr(settings, 'AUTH_USER_MODEL', 'auth.User'))
    started = models.DateTimeField(auto_now_add=True)
    unsubscribe_code = models.CharField(max_length=255, blank=False,
        unique=True, default=unique_code)
    unsubscribed = models.BooleanField(default=False)

    @staticmethod
    def for_user(user):
        """
        Returns the Subscription model for this user (or lazily creates
        one).
        """
        try:
            return Subscription.objects.get(user=user)
        except Subscription.DoesNotExist:
            sub = Subscription(user=user)
            sub.save()
            return sub

    @staticmethod
    def for_unsubscribe_code(c):
        try:
            return Subscription.objects.get(unsubscribe_code=c)
        except Subscription.DoesNotExist:
            return None

    def __str__(self):
        return '<Subscription user=%s unsubbed=%s>' % (
            self.user.username, self.unsubscribed)
