from django.contrib import admin

from .models import (
    Equipment,
    MachinePart,
    WorkOrder,
    WorkOrderChangeRequest,
    WorkOrderHistory,
)


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "location")
    search_fields = ("name", "location")


@admin.register(MachinePart)
class MachinePartAdmin(admin.ModelAdmin):
    list_display = ("name", "equipment")
    list_filter = ("equipment",)
    search_fields = ("name", "equipment__name")


class WorkOrderHistoryInline(admin.TabularInline):
    model = WorkOrderHistory
    extra = 0
    can_delete = False
    readonly_fields = ("actor", "action", "old_values", "new_values", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "equipment", "status", "start_date", "estimated_days")
    list_filter = ("status", "equipment")
    search_fields = ("title", "equipment__name")
    inlines = (WorkOrderHistoryInline,)
    readonly_fields = (
        "title",
        "description",
        "equipment",
        "machine_part",
        "supervisors",
        "technicians",
        "start_date",
        "estimated_days",
        "status",
        "created_by",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkOrderChangeRequest)
class WorkOrderChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "work_order", "requested_by", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "work_order",
        "requested_by",
        "proposed_values",
        "original_values",
        "reason",
        "status",
        "resolution_comment",
        "resolved_by",
        "created_at",
        "resolved_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkOrderHistory)
class WorkOrderHistoryAdmin(admin.ModelAdmin):
    list_display = ("work_order", "actor", "action", "created_at")
    readonly_fields = ("work_order", "actor", "action", "old_values", "new_values", "created_at")
    list_filter = ("action",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
