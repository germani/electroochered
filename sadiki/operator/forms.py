# -*- coding: utf-8 -*-
from django import forms
from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.contenttypes.generic import BaseGenericInlineFormSet
from django.forms.widgets import CheckboxSelectMultiple
from sadiki.account.forms import RequestionForm, PreferredSadikForm
from sadiki.administrator.admin import SadikAdminForm
from sadiki.anonym.forms import PublicSearchForm, RegistrationForm, \
    ProfileRegistrationForm, FormWithDocument
from sadiki.core.fields import TemplateFormField
from sadiki.core.models import SadikGroup, AgeGroup, Vacancies, \
    VACANCY_STATUS_PROVIDED, REQUESTION_IDENTITY, Sadik, Profile, Address, \
    STATUS_REQUESTER
from sadiki.core.utils import get_current_distribution_year, get_user_by_email
from sadiki.core.widgets import JqueryUIDateWidget, PrefSadiksJS


def select_list_from_qs(queryset, requestion):
    u"""Делает из queryset список для параметра choices"""
    select_list = []
    for obj in queryset:
        groups = requestion.get_sadik_groups(obj)
        select_list.append((obj.id, u'%d мест %s' % (groups[0].free_places, unicode(obj))))
    return select_list

class OperatorRequestionForm(RequestionForm):
    u"""Форма регистрации заявки через оператора"""
    pref_sadiks = forms.ModelMultipleChoiceField(label=u'Выберите приоритетные ДОУ',
        required=False, widget=PrefSadiksJS(attrs={'areas_name': "requestion-areas"}),
        queryset=Sadik.objects.filter(active_registration=True),
        help_text=u'Этот список не даёт прав на внеочередное зачисление в выбранные ДОУ')


    def create_document(self, requestion, commit=True):
        document = super(OperatorRequestionForm, self).create_document(
            requestion, commit=False)
#        документ документально подтвержден, т.к. добавлен оператором
        document.confirmed = True
        if commit:
            document.save()
        return document
    
    def save(self, *args, **kwargs):
        self.instance.status = STATUS_REQUESTER
        return super(OperatorRequestionForm, self).save(*args, **kwargs)

class OperatorProfileRegistrationForm(ProfileRegistrationForm):
    u"""Форма создания пользовательского профиля через оператора"""

    def create_document(self, profile, commit=True):
        document = super(OperatorProfileRegistrationForm, self).create_document(
            profile, commit=False)
        document.confirmed = True
        if commit:
            document.save()
        return document

class OperatorRegistrationForm(RegistrationForm):
    u"""Форма для регистрации пользователя через оператора"""
    email = forms.EmailField(label=u'Электронная почта',
        help_text=u'''
            Если у пользователя есть электронная почта, то укажите её, чтобы 
            пользователь впоследствии мог сам управлять своими заявками.''',
        required=False)

    class Meta(RegistrationForm.Meta):
        fields = ('email',)

    def __init__(self, password=None, *args, **kwargs):
        super(OperatorRegistrationForm, self).__init__(*args, **kwargs)
        self.fields.pop('password1')
        self.fields.pop('password2')
        self.password = password

    def save(self, commit=True):
        user = super(OperatorRegistrationForm, self).save(commit=False)
        if user.email and self.password:
            user.set_password(self.password)
        else:
            user.set_unusable_password()
        if commit:
            user.save()
        return user


class OperatorSearchForm(PublicSearchForm):
    requestion_number = forms.CharField(label=u'Номер заявки в системе',
        required=False, widget=forms.TextInput(attrs={'data-mask': u'99999999999-Б-999999999'}))
    birth_date = forms.DateField(label=u'Дата рождения ребёнка',
            widget=JqueryUIDateWidget(), required=False)

    field_map = {
        'requestion_number': 'requestion_number__exact',
        'birth_date': 'birth_date__exact',
        'registration_date': 'registration_datetime__range',
        'number_in_old_list': 'number_in_old_list__exact',
        'parent_last_name': 'profile__last_name__icontains',
        'child_last_name': 'last_name__icontains',
        'document_number': 'id__in'
    }

    def build_query(self):
        if self.cleaned_data:
            filter_kwargs = PublicSearchForm.build_query(self)
            if filter_kwargs is None:
                filter_kwargs = {}
            if 'requestion_number' in self.changed_data:
                filter_kwargs[self.field_map['requestion_number']] = self.cleaned_data['requestion_number']
            return filter_kwargs

    def clean(self):
        if not [value for key, value in self.cleaned_data.iteritems() if value]:
            raise forms.ValidationError(u"Необходимо указать параметры для поиска")
        else:
            return self.cleaned_data


class SadikGroupForm(forms.ModelForm):

    age_group = forms.ModelChoiceField(queryset=AgeGroup.objects.all(),
        label=u'Возрастная категория')

    def __init__(self, *args, **kwds):
        super(SadikGroupForm, self).__init__(*args, **kwds)
        # Для новых групп исключить изменение возрастной категории
        if self.instance.pk:
            del self.fields['age_group']

    class Meta:
        model = SadikGroup

    def save(self, commit=True):
        free_places = self.cleaned_data.get('free_places')
        if self.initial:
            # изменение группы
            places_difference = free_places - self.initial.get('free_places')
            self.instance.capacity += places_difference
        else:
            # Создание новой группы
            self.instance.capacity = free_places
            self.instance.year = get_current_distribution_year()
            self.instance.min_birth_date = self.cleaned_data['age_group'].min_birth_date()
            self.instance.max_birth_date = self.cleaned_data['age_group'].max_birth_date()
        return super(SadikGroupForm, self).save(commit)


class SadikForm(forms.Form):

    def __init__(self, sadiks_query, *args, **kwargs):
        super(SadikForm, self).__init__(*args, **kwargs)
        self.fields['sadik'] = forms.ModelChoiceField(queryset=sadiks_query,
            label=u'Выберите ДОУ', required=False)


class RequestionsFromDistributedForm(forms.Form):
    u"""
    Форма выбора из текущего комплектования заявок к ручному комплектованию
    """

    def __init__(self, distribution, *args, **kwds):
        self.distribution = distribution
        super(RequestionsFromDistributedForm, self).__init__(*args, **kwds)
        self.fields['vacancies'] = forms.ModelMultipleChoiceField(
            label=u"Путевки для освобождения",
            queryset=Vacancies.objects.filter(distribution=self.distribution,
                status=VACANCY_STATUS_PROVIDED),
            widget=CheckboxSelectMultiple()
        )


class DocumentGenericInlineFormSet(BaseGenericInlineFormSet):

    def save_new(self, form, commit=True):
#        для новых документов задается подтверждение
        instance = super(DocumentGenericInlineFormSet, self).save_new(
            form, commit=False)
        instance.confirmed = True
        if commit:
            instance.save()
            form.save_m2m()
        return instance


class RequestionIdentityDocumentForm(FormWithDocument):
    template = TemplateFormField(destination=REQUESTION_IDENTITY,
        label=u'Тип документа')

    def create_document(self, requestion, commit=True):
        document = super(RequestionIdentityDocumentForm, self).create_document(
            requestion, commit=False)
        document.confirmed = True
        if commit:
            document.save()
        return document

    def save(self, commit=True):
        requestion = super(RequestionIdentityDocumentForm, self).save(commit)
        if commit:
            self.create_document(requestion)
        return requestion


class ChangeSadikForm(SadikAdminForm):
    class Meta(SadikAdminForm.Meta):
        fields = ('postindex', 'street', 'building_number', 'email', 'site',
            'head_name', 'phone', 'cast', 'tech_level', 'training_program',
            'route_info', 'extended_info', 'active_registration',
            'active_distribution', 'age_groups',)
        
    def __init__(self, *args, **kwargs):
        map_widget = admin.site._registry[Address].get_map_widget(Address._meta.get_field_by_name('coords')[0])
        self.base_fields['coords'].widget = map_widget()
        super(ChangeSadikForm, self).__init__(*args, **kwargs)

    def save(self, commit=True):
        """
        Given a model instance save it to the database.
        """
        sadik = super(ChangeSadikForm, self).save(commit)

        if commit:
            address, created = self.get_address()
            sadik.address = address
            sadik.save()
        return sadik


class BaseConfirmationFormMixin(object):

    def __init__(self, *args, **kwargs):
        self.base_fields['reason'] = forms.CharField(label=u"Основание",
            help_text=u"Внимание! Эта информация будет публично доступной, старайтесь не указывать персональные данные",
            widget=forms.Textarea(attrs={'rows': 2, 'cols': 12}))
        super(BaseConfirmationFormMixin, self).__init__(*args, **kwargs)


class BaseConfirmationForm(BaseConfirmationFormMixin, forms.Form):
    pass


class ConfirmationFormMixin(BaseConfirmationFormMixin):

    def __init__(self, *args, **kwargs):
        self.base_fields['confirm'] = forms.BooleanField(initial=True, widget=forms.HiddenInput())
        self.base_fields['transition'] = forms.IntegerField(widget=forms.HiddenInput())
        super(ConfirmationFormMixin, self).__init__(*args, **kwargs)


class ConfirmationForm(ConfirmationFormMixin, forms.Form):

    def __init__(self, requestion, *args, **kwds):
        self.requestion = requestion
        super(ConfirmationForm, self).__init__(*args, **kwds)


class PreferredSadikConfirmationForm(ConfirmationFormMixin, PreferredSadikForm):

    def __init__(self, requestion, *args, **kwargs):
        kwargs.update({'instance': requestion})
        super(PreferredSadikConfirmationForm, self).__init__(*args, **kwargs)


class TempDistributionConfirmationForm(ConfirmationForm):
    sadik = forms.ModelChoiceField(queryset=Sadik.objects.all(), label="Выберите ДОУ")

    def __init__(self, *args, **kwds):
        super(TempDistributionConfirmationForm, self).__init__(*args, **kwds)
        vacancies_query = self.requestion.available_temp_vacancies()
        sadik_query = Sadik.objects.filter(id__in=vacancies_query.values_list('sadik_group__sadik__id'))
        self.fields['sadik'].queryset = sadik_query


class ImmediatelyDistributionConfirmationForm(ConfirmationForm):
    sadik = forms.ModelChoiceField(queryset=Sadik.objects.all(), label="Выберите ДОУ")

    def __init__(self, *args, **kwds):
        super(ImmediatelyDistributionConfirmationForm, self).__init__(*args, **kwds)
        available_sadiks_ids = self.requestion.get_sadiks_groups(
            ).values_list('sadik', flat=True)
        preferred_sadiks = self.requestion.pref_sadiks.filter(
            id__in=available_sadiks_ids)
        any_sadiks = Sadik.objects.exclude(id__in=preferred_sadiks).filter(
            id__in=available_sadiks_ids)

        choices = []
        if preferred_sadiks:
            choices.append((u'Предпочитаемые ДОУ', select_list_from_qs(preferred_sadiks, self.requestion)))
        else:
            choices.append((u'ДОУ этой территориальной области', select_list_from_qs(any_sadiks, self.requestion)))

        self.fields['sadik'].queryset = Sadik.objects.filter(id__in=available_sadiks_ids)
        self.fields['sadik'].choices = choices


class EmailForm(forms.ModelForm):

    class Meta:
        model = User
        fields = ("email",)

    def clean_email(self):
        if get_user_by_email(self.cleaned_data.get('email', '')):
            raise forms.ValidationError(u'Такой адрес электронной почты уже зарегистрирован.')
        return self.cleaned_data['email']


class ProfileSearchForm(forms.Form):
    requestion_number = forms.CharField(
        label=u'Номер заявки привязанной к данному профилю',
        required=False,
        widget=forms.TextInput(attrs={'data-mask': u'99999999999-Б-999999999'}))
    email = forms.EmailField(
        label=u"Адрес электронной почты", required=False)
    last_name = forms.CharField(
        label=u'Фамилия родителя', required=False, widget=forms.TextInput())
    first_name = forms.CharField(
        label=u'Имя родителя', required=False, widget=forms.TextInput())
    parent_last_name = forms.CharField(
        label=u'Отчество родителя', required=False, widget=forms.TextInput())

    field_map = {
        'requestion_number': 'requestion__requestion_number__exact',
        'email': 'user__email__exact',
        'last_name': 'last_name__icontains',
        'first_name': 'first_name__icontains',
        'patronymic': 'patronymic__icontains',
    }

    def __init__(self, *args, **kwds):
        super(ProfileSearchForm, self).__init__(*args, **kwds)
        self.reverse_field_map = dict((v, k) for k, v in self.field_map.iteritems())

    def build_query(self):
        if self.cleaned_data:
            filter_kwargs = {}
            if 'requestion_number' in self.changed_data:
                filter_kwargs[self.field_map['requestion_number']] = self.cleaned_data['requestion_number']
            if 'email' in self.changed_data:
                filter_kwargs[self.field_map['email']] = self.cleaned_data['email']
            if 'last_name' in self.changed_data:
                filter_kwargs[self.field_map['last_name']] = self.cleaned_data['last_name']
            if 'first_name' in self.changed_data:
                filter_kwargs[self.field_map['first_name']] = self.cleaned_data['first_name']
            if 'patronymic' in self.changed_data:
                filter_kwargs[self.field_map['patronymic']] = self.cleaned_data['patronymic']
            return filter_kwargs
