"""Admin for control-plane models."""

from django.contrib import admin

from peon.projects.models import Finding, Job, JobDirective, Objective, Project, RulesOfEngagement, RuntimeSettings, StreamMessage


class RulesOfEngagementInline(admin.StackedInline):
    model = RulesOfEngagement
    extra = 0
    max_num = 1


class ObjectiveInline(admin.TabularInline):
    model = Objective
    extra = 0
    fields = ("seq", "title", "phase", "status", "skill_suggestion", "profile_suggestion")
    show_change_link = True


class FindingInline(admin.TabularInline):
    model = Finding
    extra = 0
    fields = ("seq", "title", "severity", "status", "kind")
    show_change_link = True


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "summary")
    inlines = [RulesOfEngagementInline, ObjectiveInline, FindingInline]
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Objective)
class ObjectiveAdmin(admin.ModelAdmin):
    list_display = ("seq", "title", "project", "phase", "status", "skill_suggestion")
    list_filter = ("phase", "status")
    search_fields = ("title", "skill_suggestion")
    filter_horizontal = ("depends_on",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ("seq", "title", "project", "severity", "status", "kind")
    list_filter = ("severity", "status", "kind")
    search_fields = ("title", "host")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(RulesOfEngagement)
class RulesOfEngagementAdmin(admin.ModelAdmin):
    list_display = ("project", "updated_at")


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "lifecycle", "project", "objective", "parent", "created_at")
    list_filter = ("status", "lifecycle")
    search_fields = ("title", "description", "workspace_id")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("project", "objective", "parent")


@admin.register(RuntimeSettings)
class RuntimeSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "updated_at")
    readonly_fields = ("id", "updated_at")


@admin.register(JobDirective)
class JobDirectiveAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "kind", "created_at", "consumed_at")
    list_filter = ("kind",)
    search_fields = ("content",)
    raw_id_fields = ("job",)
    readonly_fields = ("created_at", "consumed_at")


@admin.register(StreamMessage)
class StreamMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "message_type", "created_at")
    list_filter = ("message_type",)
    search_fields = ("content",)
    raw_id_fields = ("job",)
    readonly_fields = ("created_at",)
