from django.urls import path
from . import views  

urlpatterns = [
    path('', views.loginpage, name='loginpage'),
    path('login/', views.login, name='login'),
    path('admindashboard/', views.admindashboard, name='admindashboard'),
    path('analytics/', views.analytics, name='analytics'),
    path('crm-workspace/', views.crm_workspace, name='crm_workspace'),
    path('logout/', views.logout, name='logout'),
    path('properties/', views.properties, name='properties'),
    path('agents/', views.agents, name='agents'),                                                    
    path('search/', views.search_properties, name='search_properties'),
    path('activity-logs/', views.activity_logs_view, name='activity_logs'),
    path('customer-actions/', views.customer_actions_view, name='customer_actions'), 
    path('contact-requests/', views.contact_requests_view, name='contact_requests'),
    path('daily-visits/', views.daily_visits_view, name='daily_visits'),
    path('leads_list/', views.leads_list, name='leads_list'),
    path('property-locations/', views.property_locations_view, name='property_locations'),
    path('property-location/update/<str:doc_id>/', views.update_property_location, name='update_property_location'),
    path('property-location/delete/<str:doc_id>/', views.delete_property_location, name='delete_property_location'),
    path('registrations/', views.registration_list, name='registration_list'),
    path('unfulfilled-searches/', views.unfulfilled_searches, name='unfulfilled_searches'),
    path('users/', views.user_list, name='user_list'),
    path('deleted-properties/', views.deleted_properties_view, name='deleted_properties'),
    path('add_property/', views.add_property, name='add_property'),
    path('agentsproperties/', views.agentsproperties, name='agentsproperties'),
    path('agents/update/<str:doc_id>/', views.update_agent, name='update_agent'),
    path('update-property/', views.update_property, name='update_property'),
    path('delete-property/', views.delete_property, name='delete_property'),
    path('property-location/add/', views.add_property_location, name='add_property_location'),

]
