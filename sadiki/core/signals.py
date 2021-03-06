# -*- coding: utf-8 -*-
"""
Модуль обработчиков смены статуса заявки.

Статус меняется классом ``sadiki.operator.views.RequestionStatusChange``,
при это вызывается два сигнала, ``pre_status_change`` и ``post_status_change``.

Соглашение по наименованию функций:
    before_<event> - отрабатывает до смены статуса
    after_<event> - отрабатывает после смены статуса

Для обработчика нужн указать интересующие его переходы
через декоратор ``@listen_transitions``.

Помимо обработчиков смены статуса в модуле содержатся доп. проверки
при смене статуса. см. ``workflow.py``. Например: ::

    # Функция проверки, должна возвращать True или False
    def permit_remove_registration(requestion, transition, user=None, request=None, form=None):
        print 'check decision to requester',
        return True

    # Подключение проверки к переходу:
    remove_registration_transition = workflow.get_transition_by_index(REQUESTER_REMOVE_REGISTRATION)
    remove_registration_transition.permission_cb = permit_remove_registration


"""
from django.db.models.aggregates import Sum
from django.dispatch import Signal, receiver
from sadiki.account.forms import PreferredSadikForm
from sadiki.conf_settings import TEMP_DISTRIBUTION, IMMEDIATELY_DISTRIBUTION
from sadiki.core.models import Requestion, PERMANENT_DISTRIBUTION_TYPE, \
    STATUS_REMOVE_REGISTRATION, VACANCY_STATUS_TEMP_ABSENT, STATUS_REQUESTER, \
    STATUS_WANT_TO_CHANGE_SADIK, STATUS_TEMP_DISTRIBUTED, VACANCY_STATUS_DISTRIBUTED, \
    VACANCY_STATUS_TEMP_DISTRIBUTED, SadikGroup, TRANSFER_DISTRIBUTION_TYPE
from sadiki.core.settings import TEMP_DISTRIBUTION_YES, \
    IMMEDIATELY_DISTRIBUTION_YES, IMMEDIATELY_DISTRIBUTION_FACILITIES_ONLY
from sadiki.core.workflow import REQUESTER_REMOVE_REGISTRATION, \
    NOT_CONFIRMED_REMOVE_REGISTRATION, ABSENT_REMOVE_REGISTRATION, \
    NOT_APPEAR_REMOVE_REGISTRATION, CONFIRM_REQUESTION, TEMP_ABSENT, \
    TEMP_ABSENT_CANCEL, RETURN_TEMP_DISTRIBUTED, DECISION_REQUESTER, \
    NOT_APPEAR_REQUESTER, ABSENT_REQUESTER, DECISION_TEMP_DISTRIBUTED, \
    NOT_APPEAR_TEMP_DISTRIBUTED, ABSENT_TEMP_DISTRIBUTED, \
    DECISION_WANT_TO_CHANGE_SADIK, NOT_APPEAR_WANT_TO_CHANGE_SADIK, \
    ABSENT_WANT_TO_CHANGE_SADIK, DECISION_DISTRIBUTION, NOT_APPEAR_DISTRIBUTED, \
    ABSENT_DISTRIBUTED, DECISION_NOT_APPEAR, DECISION_ABSENT, \
    TEMP_DISTRIBUTION_TRANSFER, IMMEDIATELY_DECISION, RESTORE_REQUESTION, \
    WANT_TO_CHANGE_SADIK_DISTRIBUTED, WANT_TO_CHANGE_SADIK, workflow
from sadiki.logger.models import Logger
from sadiki.operator.forms import TempDistributionConfirmationForm, \
    ImmediatelyDistributionConfirmationForm, PreferredSadikConfirmationForm
import datetime


pre_status_change = Signal(providing_args=['user', 'requestion', 'transition', 'form'])
post_status_change = Signal(providing_args=['user', 'requestion', 'transition', 'form'])


def listen_transitions(*transition_indexes):
    u"""
    Декоратор для выборки нужных переходов в workflow
    """
    def real_decorator(func):
        def wrapper(*args, **kwargs):
            transition = kwargs['transition']
            if transition.index in transition_indexes:
                return func(*args, **kwargs)
        return wrapper
    return real_decorator


@receiver(post_status_change, sender=Requestion)
@listen_transitions(
    REQUESTER_REMOVE_REGISTRATION,
    NOT_CONFIRMED_REMOVE_REGISTRATION,
    ABSENT_REMOVE_REGISTRATION,
    NOT_APPEAR_REMOVE_REGISTRATION,
)
def after_remove_registration(sender, **kwargs):
    u"""Обработчик переводов № 38, 39, 41, 42 - Снятие заявки с учёта"""
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

    log_extra = {'user': user, 'obj': requestion}
    # Если заявитель не явился за путевой, освободить его место в группе
    if transition in (ABSENT_REMOVE_REGISTRATION, NOT_APPEAR_REMOVE_REGISTRATION):
        sadik_group = requestion.distributed_in_vacancy.sadik_group
        sadik_group.free_places += 1
        sadik_group.save()
        requestion.vacate_previous_place()
#        запишем в логи какой тип распределения производился
        log_extra.update({'distribution_type': requestion.distribution_type})
    # убрать подтверждение с докумнетов
    requestion.status = STATUS_REMOVE_REGISTRATION
    requestion.save()
    requestion.set_document_unauthentic()

    context_dict = {'status': requestion.get_status_display()}
    Logger.objects.create_for_action(
        transition.index, context_dict=context_dict, extra=log_extra,
        reason=form.cleaned_data.get('reason'))

    user.message_set.create(message=u'Заявка %s была снята с учета' % requestion.requestion_number)


@receiver(post_status_change, sender=Requestion)
@listen_transitions(
    CONFIRM_REQUESTION,
)
def after_set_documental_confirmation(sender, **kwargs):
    u"""Обработчик перевода №3 - Подтверждение заявки"""
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']
    other_requestions_with_document = requestion.set_ident_document_authentic()
    requestion.set_benefit_documents_authentic()
    context_dict = {'other_requestions': other_requestions_with_document}
    Logger.objects.create_for_action(transition.index,
        context_dict=context_dict,
        extra={'user': user, 'obj': requestion}, reason=form.cleaned_data.get('reason'))
    user.message_set.create(message=u'Заявка %s была документально подтверждена' % requestion.requestion_number)
    if other_requestions_with_document:
        user.message_set.create(message=u'Следующие заявки имели такой же идентифицирующий документ и были сняты с учета: %s' %
            ";".join([unicode(other_requestion) for other_requestion in other_requestions_with_document]))


@receiver(post_status_change, sender=Requestion)
@listen_transitions(
    TEMP_ABSENT,
)
def after_set_temp_absent(sender, **kwargs):
    u"""Обработчик перевода №56 Временное отсутсвие (при временном зачислении)
    по уважиетльной причине. Например, поехал в отпуск на полгода"""
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

    vacancy = requestion.distributed_in_vacancy
    vacancy.status = VACANCY_STATUS_TEMP_ABSENT
    vacancy.save()
    Logger.objects.create_for_action(transition.index,
        extra={'user': user, 'obj': requestion}, reason=form.cleaned_data.get('reason'))
    user.message_set.create(message=u'''Заявка %s переведена
    в статус отсутствия по уважительной причине''' % requestion.requestion_number)


@receiver(post_status_change, sender=Requestion)
@listen_transitions(
    TEMP_ABSENT_CANCEL,
)
def after_cancel_temp_absent(sender, **kwargs):
    u"""Обработчик перевода №40 Восстановление в очереди"""
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

    Logger.objects.create_for_action(transition.index,
        extra={'user': user, 'obj': requestion}, reason=form.cleaned_data.get('reason'))
    user.message_set.create(message=u'''Заявка %s была возвращена в ДОУ после отсутствия.
        ''' % requestion.requestion_number)

    temp_distributed_requestion = requestion.distributed_in_vacancy.get_distributed_requestion()
    if temp_distributed_requestion:
        temp_distributed_requestion.status = STATUS_REQUESTER
        temp_distributed_requestion.save()
        Logger.objects.create_for_action(RETURN_TEMP_DISTRIBUTED,
            extra={'user': user, 'obj': temp_distributed_requestion})
        user.message_set.create(message=u'''Заявка %s была возвращена в очередь в связи с восстановлением
            временно отсутсвующей''' % requestion.requestion_number)


@receiver(post_status_change, sender=Requestion)
@listen_transitions(
    DECISION_REQUESTER, NOT_APPEAR_REQUESTER, ABSENT_REQUESTER,
    DECISION_TEMP_DISTRIBUTED, NOT_APPEAR_TEMP_DISTRIBUTED,
    ABSENT_TEMP_DISTRIBUTED, DECISION_WANT_TO_CHANGE_SADIK,
    NOT_APPEAR_WANT_TO_CHANGE_SADIK, ABSENT_WANT_TO_CHANGE_SADIK,
)
def after_decision_reject(sender, **kwargs):
    u"""Обработчик переводов №46, 52, 53 - возвращение заявки обратно в очередь"""
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

    # Пересчитать кол-во мест в группе
    sadik_group = requestion.distributed_in_vacancy.sadik_group
    sadik_group.free_places += 1
    sadik_group.save()
#    если заявка уже была распределена, то возвращаем путевку на место
    if transition.dst in (STATUS_WANT_TO_CHANGE_SADIK, STATUS_TEMP_DISTRIBUTED):
        requestion.distributed_in_vacancy = requestion.previous_distributed_in_vacancy
    # Журналирование
    user.message_set.create(message=u'Заявка %s была возвращена в очередь.' % requestion.requestion_number)
    context_dict = {'status': requestion.get_status_display()}
    Logger.objects.create_for_action(transition.index,
        context_dict=context_dict,
        extra={'user': user, 'obj': requestion,
            'distribution_type': requestion.distribution_type},
        reason=form.cleaned_data.get('reason'))


@receiver(post_status_change, sender=Requestion)
@listen_transitions(
    DECISION_DISTRIBUTION,
    NOT_APPEAR_DISTRIBUTED,
    ABSENT_DISTRIBUTED,
)
def after_decision_to_distributed(sender, **kwargs):
    u"""Обработчик переводов №16,19,20 - зачисление в ДОУ"""
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

    requestion.vacate_previous_place()
    requestion.distributed_in_vacancy.status = VACANCY_STATUS_DISTRIBUTED
    requestion.distributed_in_vacancy.save()
    user.message_set.create(message=u'''Заявка %s была зачислена в %s.
            ''' % (requestion.requestion_number,
                requestion.distributed_in_vacancy.sadik_group.sadik))
    context_dict = {'status': requestion.get_status_display()}
    log_extra = {'user': user, 'obj': requestion,
        'distribution_type': requestion.distribution_type}
    Logger.objects.create_for_action(transition.index,
        context_dict=context_dict, extra=log_extra,
        reason=form.cleaned_data.get('reason'))


@receiver(post_status_change, sender=Requestion)
@listen_transitions(
    DECISION_NOT_APPEAR,
)
def after_decision_not_appear(sender, **kwargs):
    u"""Обработчик перевода №18 - Отметка о неявке"""
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

    user.message_set.create(
        message=u'Для заявки %s была отмечена неявка в ДОУ' % requestion.requestion_number
    )
    context_dict = {'status': requestion.get_status_display()}
    Logger.objects.create_for_action(transition.index,
        context_dict=context_dict,
        extra={'user': user, 'obj': requestion,
            'distribution_type': requestion.distribution_type},
        reason=form.cleaned_data.get('reason'))


@receiver(post_status_change, sender=Requestion)
@listen_transitions(
    DECISION_ABSENT,
)
def after_decision_absent(sender, **kwargs):
    u"""Обработчик перевода №18 - Отметка о неявке"""
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

    user.message_set.create(
        message=u"""Для заявки %s была отмечена невозможность
            связаться с заявителем""" % requestion.requestion_number)
    context_dict = {'status': requestion.get_status_display()}
    log_extra = {'user': user, 'obj': requestion,
        'distribution_type': requestion.distribution_type}
    Logger.objects.create_for_action(transition.index,
        context_dict=context_dict, extra=log_extra,
        reason=form.cleaned_data.get('reason'))

# Временное зачисление
if TEMP_DISTRIBUTION == TEMP_DISTRIBUTION_YES:
    @receiver(post_status_change, sender=Requestion)
    @listen_transitions(
        TEMP_DISTRIBUTION_TRANSFER,
    )
    def after_temp_distribution(sender, **kwargs):
        u"""Обработчик перехода №35 - временное зачисление
        у данного перевода переопределены форма подтверждения и функция доп. проверки
        """
        transition = kwargs['transition']
        form = kwargs['form']
        user = kwargs['user']
        requestion = kwargs['requestion']

        sadik = form.cleaned_data.get("sadik")

        vacancy = requestion.available_temp_vacancies().filter(sadik_group__sadik=sadik)[0]
        vacancy.status = VACANCY_STATUS_TEMP_DISTRIBUTED
        vacancy.save()
        requestion.distributed_in_vacancy = vacancy
        requestion.save()

        user.message_set.create(message=u'''Заявка %s была временно зачислена в %s.
                ''' % (requestion.requestion_number, vacancy.sadik_group.sadik))
        log_extra = {'user': user, 'obj': requestion, }
        Logger.objects.create_for_action(transition.index,
            extra=log_extra, reason=form.cleaned_data.get('reason'))

    @receiver(post_status_change, sender=Requestion)
    @listen_transitions(
        RETURN_TEMP_DISTRIBUTED,
    )
    def after_temp_distribution_cancel(sender, **kwargs):
        u"""Обработчик перевода № 48 - отмена временного зачисления"""
        transition = kwargs['transition']
        user = kwargs['user']
        requestion = kwargs['requestion']
        form = kwargs['form']

        vacancy = requestion.distributed_in_vacancy
        vacancy.status = VACANCY_STATUS_TEMP_ABSENT
        vacancy.save()
        requestion.status = STATUS_REQUESTER
        requestion.save()
        user.message_set.create(message=u'Для заявки %s было отменено временное распределение'
            % requestion.requestion_number)
        Logger.objects.create_for_action(transition.index,
            extra={'user': user, 'obj': requestion, },
            reason=form.cleaned_data.get('reason'))

# Немедленное зачисление
if IMMEDIATELY_DECISION in (IMMEDIATELY_DISTRIBUTION_YES, IMMEDIATELY_DISTRIBUTION_FACILITIES_ONLY):
    @receiver(post_status_change, sender=Requestion)
    @listen_transitions(
        IMMEDIATELY_DECISION,
    )
    def after_immediately_decision(sender, **kwargs):
        transition = kwargs['transition']
        form = kwargs['form']
        user = kwargs['user']
        requestion = kwargs['requestion']

        sadik = form.cleaned_data.get("sadik")
        vacancy = requestion.distribute_in_sadik(sadik)
        user.message_set.create(message=u'Заявке %s было выделено место в %s.'
            % (requestion.requestion_number, vacancy.sadik_group.sadik))
        Logger.objects.create_for_action(transition.index,
            extra={'user': user, 'obj': requestion, }, reason=form.cleaned_data.get('reason'))


@receiver(post_status_change, sender=Requestion)
@listen_transitions(
    RESTORE_REQUESTION,
)
def after_restore_requestion(sender, **kwargs):
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

    other_requestions_with_document = requestion.set_ident_document_authentic()
    user.message_set.create(message=u'''Заявка %s была возвращена в очередь.
                    ''' % requestion.requestion_number)
    user.message_set.create(message=u'Следующие заявки имели такой же идентифицирующий документ и были сняты с учета: %s' %
        ";".join([unicode(other_requestion) for other_requestion in other_requestions_with_document]))
    Logger.objects.create_for_action(transition.index,
        context_dict={'other_requestions': other_requestions_with_document},
        extra={'user': user, 'obj': requestion},
        reason=form.cleaned_data.get('reason'))


@receiver(post_status_change, sender=Requestion)
@listen_transitions(WANT_TO_CHANGE_SADIK_DISTRIBUTED,)
def after_want_to_change_sadik_distributed(sender, **kwargs):
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

    user.message_set.create(message=u"Запрос на смену ДОУ был отменен")
    log_extra = {'user': user, 'obj': requestion,
            'distribution_type': requestion.distribution_type,
            'removed_pref_sadiks': requestion.pref_sadiks.all()}
    Logger.objects.create_for_action(
        transition.index, extra=log_extra,
        reason=form.cleaned_data.get('reason'))


@receiver(post_status_change, sender=Requestion)
@listen_transitions(WANT_TO_CHANGE_SADIK,)
def after_want_to_change_sadik(sender, **kwargs):
    transition = kwargs['transition']
    user = kwargs['user']
    requestion = kwargs['requestion']
    form = kwargs['form']

#    при переводе заявка ставится в конец очереди
    requestion.registration_datetime = datetime.datetime.now()
    requestion.save()

    user.message_set.create(
        message=u'Заявка переведена в статус "Желает сменить ДОУ"')
    Logger.objects.create_for_action(
        transition.index,
        extra={'user': user, 'obj': requestion,
            'added_pref_sadiks': requestion.pref_sadiks.all()},
        reason=form.cleaned_data.get('reason'))


# ------------------------------------------------------
# Функции дополнительной проеврки переходов (callback)
# ------------------------------------------------------
def register_callback(transitions, callback):
    u"""Функция регистрации дополнительной проверки"""
    if not isinstance(transitions, (list, tuple)):
        transitions = (transitions,)
    for transition_index in transitions:
        transition = workflow.get_transition_by_index(transition_index)
        transition.permission_cb = callback


def register_form(transition_index, form_cls):
    u"""Функция регистрации другой формы подтверждения"""
    transition = workflow.get_transition_by_index(transition_index)
    transition.confirmation_form_class = form_cls


register_form(WANT_TO_CHANGE_SADIK, PreferredSadikConfirmationForm)

# Временное зачисление
if TEMP_DISTRIBUTION == TEMP_DISTRIBUTION_YES:
    def permit_temp_distribution(user, requestion, transition, request=None, form=None):
        u"""
        Доп. проверка к переходу № 35 - временное зачисление.

        Проверка что заявку можно куда-нибудь временно зачислить
        Проверяется наличие временно свободных мест в нужной возрастной группе,
        вызывается метод ``Requestion.available_temp_vacancies``
        """
        return requestion.available_temp_vacancies().exists()

    # Изменение поведения при временном зачислении
    register_callback(TEMP_DISTRIBUTION_TRANSFER, permit_temp_distribution)
    register_form(TEMP_DISTRIBUTION_TRANSFER, TempDistributionConfirmationForm)

    def permit_permanent_decision_reject(
            user, requestion, transition, request=None, form=None):
        u"""
        Проверка переходов при отказе от зачисления на постоянной основе
        временно зачисленных 
        """
        return requestion.distribution_type == PERMANENT_DISTRIBUTION_TYPE

    register_callback((DECISION_TEMP_DISTRIBUTED, NOT_APPEAR_TEMP_DISTRIBUTED,
        ABSENT_TEMP_DISTRIBUTED), permit_permanent_decision_reject)

# Немедленное зачисление
if IMMEDIATELY_DISTRIBUTION in (IMMEDIATELY_DISTRIBUTION_YES,
        IMMEDIATELY_DISTRIBUTION_FACILITIES_ONLY):
    def permit_immediately_decision(user, requestion, transition, request=None, form=None):
        u"""
        Доп проверка к переходу №8 - немедленное зачисление.
        """
        # если немедленное распределение распространяется только на отдельные
        # категории льгот, то проверяем, что у заявки нужная категория
        if IMMEDIATELY_DISTRIBUTION == IMMEDIATELY_DISTRIBUTION_FACILITIES_ONLY:
            if not requestion.benefits.filter(category__immediately_distribution_active=True).exists():
                return False
        if not requestion.age_groups():
            return False
        age_group = requestion.age_groups()[0]
        min_birth_date = age_group.min_birth_date
        max_birth_date = age_group.max_birth_date

        # Нужно взять минимальный и максимальный возраст их всех доступных групп
        for group in requestion.age_groups:
            if group.min_birth_date < min_birth_date:
                min_birth_date = group.min_birth_date
            if group.max_birth_date > max_birth_date:
                max_birth_date = group.max_birth_date

        if requestion.get_sadiks_groups().exists():
            queue = Requestion.objects.queue().confirmed()
            queue_for_group_count = queue.filter_for_age(
                min_birth_date=min_birth_date, max_birth_date=max_birth_date).count()
        else:
            queue_for_group_count = 0

        free_places = SadikGroup.objects.appropriate_for_birth_date(
            requestion.birth_date).aggregate(Sum('free_places'))['free_places__sum']

        return free_places > queue_for_group_count

    register_callback(IMMEDIATELY_DECISION, permit_immediately_decision)
    register_form(IMMEDIATELY_DECISION, ImmediatelyDistributionConfirmationForm)


def permit_restore_requestion(user, requestion, transition, request=None, form=None):
    return not requestion.get_other_ident_documents(confirmed=True).exists()

register_callback(RESTORE_REQUESTION, permit_restore_requestion)


def permit_distribution(user, requestion, transition, request=None, form=None):
    return user.perms_for_area(requestion.distributed_in_vacancy.sadik_group.sadik.area)

register_callback(
    (DECISION_DISTRIBUTION, NOT_APPEAR_DISTRIBUTED, ABSENT_DISTRIBUTED),
    permit_distribution)


def permit_transfer_reject(user, requestion, transition, request=None, form=None):
    return requestion.distribution_type == TRANSFER_DISTRIBUTION_TYPE

register_callback((DECISION_WANT_TO_CHANGE_SADIK,
    NOT_APPEAR_WANT_TO_CHANGE_SADIK, ABSENT_WANT_TO_CHANGE_SADIK,),
    permit_transfer_reject)
